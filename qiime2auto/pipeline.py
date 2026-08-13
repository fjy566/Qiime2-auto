"""QIIME2 分析流程编排。

本模块只在用户真正执行分析时调用外部命令；扫描、模板和 Web 控制台不依赖本模块。
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AnalysisConfig
from .io import create_output_structure, generate_report
from .preflight import inspect_metadata_capabilities
from .runner import CommandError, CommandRunner


@dataclass
class PipelineOptions:
    input_path: str
    output_dir: str
    data_type: str
    barcodes: str | None = None
    primer_f: str | None = None
    primer_r: str | None = None
    primer_metadata: str | None = None
    primer_f_col: str = "primer_f"
    primer_r_col: str = "primer_r"
    no_trim: bool = False
    no_filter: bool = False
    no_figaro: bool = False
    skip_taxonomy: bool = False
    skip_diversity: bool = False
    skip_ancom: bool = False
    dry_run: bool = False
    qiime_env: str | None = None

    @property
    def paired_end(self) -> bool:
        return "paired" in self.data_type


@dataclass
class PipelineResult:
    success: bool
    output_dir: str
    report: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output_dir": self.output_dir,
            "report": self.report,
            "steps": self.steps,
            "error": self.error,
        }


class PipelineService:
    def __init__(self, config: AnalysisConfig, options: PipelineOptions):
        config.validate()
        self.config = config
        self.options = options
        self.output_dir = Path(create_output_structure(options.output_dir))
        command_prefix = []
        if options.qiime_env:
            command_prefix = ["conda", "run", "--no-capture-output", "-n", options.qiime_env]
        self.runner = CommandRunner(self.output_dir, dry_run=options.dry_run, command_prefix=command_prefix)
        self.steps: list[dict[str, Any]] = []

    def _step(self, name: str, action):
        print(f"\n━━ {name} ━━")
        try:
            value = action()
        except CommandError:
            raise
        self.steps.append({"name": name, "status": "completed", "output": str(value) if value else None})
        return value

    def run(self) -> PipelineResult:
        try:
            self.runner.require(["conda" if self.options.qiime_env else "qiime"])
            demux_file = self._step("导入数据", self._import_data)
            if not demux_file:
                raise RuntimeError("数据导入未产生输出文件")

            if not self.options.no_trim and (self.options.primer_f or self.options.primer_r or self.options.primer_metadata):
                demux_file = self._step("去除引物", lambda: self._remove_primers(demux_file))
                if not demux_file:
                    raise RuntimeError("去除引物失败")

            if not self.options.no_filter:
                filtered = self._step("质量过滤", lambda: self._filter_low_quality(demux_file))
                if filtered:
                    demux_file = filtered

            figaro_data = None
            if not self.options.no_figaro:
                figaro_data = self._step("Figaro 截断建议", lambda: self._run_figaro(demux_file))

            table, rep_seqs = self._step("DADA2 去噪", lambda: self._run_dada2(demux_file, figaro_data))
            metadata_capabilities = inspect_metadata_capabilities(self.config.metadata)

            depth_info = None
            if self.options.skip_diversity:
                self.steps.append({"name": "采样深度", "status": "skipped", "output": "已跳过多样性分析，不需要采样深度"})
            elif not metadata_capabilities["usable"]:
                self.steps.append({"name": "采样深度", "status": "skipped", "output": "没有可用 metadata，不需要计算采样深度"})
            else:
                depth_step_index = len(self.steps)
                depth_info = self._step("计算采样深度", lambda: self._calculate_sampling_depth(table))
                if depth_info is None:
                    self.steps[depth_step_index] = {"name": "采样深度", "status": "skipped", "output": "无法自动确定采样深度，已跳过多样性分析；请改用正整数后重试"}

            taxonomy = None
            if self.options.skip_taxonomy:
                self.steps.append({"name": "物种分类", "status": "skipped", "output": "你选择了跳过物种分类"})
            else:
                taxonomy = self._step("物种分类", lambda: self._run_taxonomy(rep_seqs, table))
                if not taxonomy:
                    print("⚠️ 分类失败，已跳过依赖分类结果的步骤")

            diversity_dir = None
            if self.options.skip_diversity:
                self.steps.append({"name": "多样性分析", "status": "skipped", "output": "你选择了跳过多样性分析"})
            elif metadata_capabilities["usable"]:
                if depth_info is None:
                    self.steps.append({"name": "多样性分析", "status": "skipped", "output": "无法确定采样深度，已跳过 core metrics"})
                else:
                    diversity_dir = self._step("多样性分析", lambda: self._run_diversity(table, rep_seqs, depth_info))
            else:
                self.steps.append({"name": "多样性分析", "status": "skipped", "output": "没有可用 metadata，已跳过 core metrics"})

            ancom = None
            if self.options.skip_ancom:
                self.steps.append({"name": "ANCOM 差异分析", "status": "skipped", "output": "你选择了跳过 ANCOM 差异分析"})
            elif not taxonomy:
                self.steps.append({"name": "ANCOM 差异分析", "status": "skipped", "output": "没有 taxonomy，已跳过差异分析"})
            elif self._group_column():
                ancom = self._step("ANCOM 差异分析", lambda: self._run_ancom(table, taxonomy))
            else:
                self.steps.append({"name": "ANCOM 差异分析", "status": "skipped", "output": "没有至少两个完整值的 categorical 分组列"})

            rooted_tree = self.output_dir / "06_phylogeny" / "rooted-tree.qza"
            if not self.options.skip_diversity and metadata_capabilities["usable"] and depth_info is not None and (rooted_tree.exists() or self.options.dry_run):
                self._step("导出系统发育树", lambda: self._visualize_phylogeny(rooted_tree))
            else:
                self.steps.append({"name": "系统发育树", "status": "skipped", "output": "多样性分析未执行，已跳过系统发育树导出"})

            params = {**self.config.as_dict(), **self.options.__dict__}
            report = generate_report(self.output_dir, params, depth_info)
            return PipelineResult(True, str(self.output_dir), report, self.steps)
        except (CommandError, RuntimeError, ValueError) as exc:
            self.steps.append({"name": "流程终止", "status": "failed", "output": str(exc)})
            print(f"❌ 分析失败: {exc}")
            return PipelineResult(False, str(self.output_dir), None, self.steps, str(exc))

    def _import_data(self) -> str:
        output = self.output_dir / "00_raw_data" / "demux.qza"
        input_path = Path(self.options.input_path)
        if self.options.data_type in {"muxed_single", "muxed_paired"}:
            if not self.options.barcodes:
                raise ValueError("混样数据必须提供 --barcodes")
            r1 = str(input_path)
            if self.options.paired_end:
                r2 = r1.replace("_R1_", "_R2_")
                if not Path(r2).exists():
                    raise ValueError(f"找不到双端反向文件: {r2}")
                import_cmd = ["qiime", "tools", "import", "--type", "MultiplexedPairedEndBarcodeInSequence", "--input-path", r1, "--input-path", r2, "--output-path", str(output)]
            else:
                import_cmd = ["qiime", "tools", "import", "--type", "MultiplexedSingleEndBarcodeInSequence", "--input-path", r1, "--output-path", str(output)]
            self.runner.run(import_cmd, "pipeline.log")
            demux_output = self.output_dir / "01_demultiplexed" / "demultiplexed.qza"
            command = ["qiime", "demux", "emp-paired" if self.options.paired_end else "emp-single", "--m-barcodes-file", self.options.barcodes, "--m-barcodes-column", "barcode-sequence", "--i-seqs", str(output), "--p-rev-comp-mapping-barcodes", "--p-barcode-in-sequence", "--o-per-sample-sequences", str(demux_output)]
            self.runner.run(command, "pipeline.log")
            return str(demux_output)

        type_map = {
            "EMP_single": ["--type", "EMPSingleEndSequences"],
            "EMP_paired": ["--type", "EMPPairedEndSequences"],
            "Casava_single": ["--type", "SampleData[SequencesWithQuality]", "--input-format", "CasavaOneEightSingleLanePerSampleDirFmt"],
            "Casava_paired": ["--type", "SampleData[PairedEndSequencesWithQuality]", "--input-format", "CasavaOneEightSingleLanePerSampleDirFmt"],
            "manifest_single": ["--type", "SampleData[SequencesWithQuality]", "--input-format", f"SingleEndFastqManifestPhred{'33' if self.config.phred_offset == 33 else '64'}V2"],
            "manifest_paired": ["--type", "SampleData[PairedEndSequencesWithQuality]", "--input-format", f"PairedEndFastqManifestPhred{'33' if self.config.phred_offset == 33 else '64'}V2"],
        }
        if self.options.data_type not in type_map:
            raise ValueError(f"不支持的数据类型: {self.options.data_type}")
        command = ["qiime", "tools", "import", *type_map[self.options.data_type], "--input-path", str(input_path), "--output-path", str(output)]
        self.runner.run(command, "pipeline.log")
        return str(output)

    def _remove_primers(self, demux_file: str) -> str:
        output = self.output_dir / "01_demultiplexed" / "trimmed.qza"
        if self.options.primer_metadata and not Path(self.options.primer_metadata).is_file():
            raise ValueError(f"样本特异性引物文件不存在: {self.options.primer_metadata}")
        if self.options.primer_metadata and not (self.options.primer_f or self.options.primer_r):
            raise ValueError("当前流程不会静默忽略 primer-metadata；请先提供 --primer-f/--primer-r，或在外部完成样本特异性引物展开")
        if self.options.paired_end:
            command = ["qiime", "cutadapt", "trim-paired", "--i-demultiplexed-sequences", demux_file, "--o-trimmed-sequences", str(output), "--p-cores", str(os.cpu_count() or 4), "--p-forward-cut", str(self.config.trim_left_f), "--p-reverse-cut", str(self.config.trim_left_r)]
            if self.options.primer_f:
                command.extend(["--p-front-f", self.options.primer_f])
            if self.options.primer_r:
                command.extend(["--p-front-r", self.options.primer_r])
        else:
            command = ["qiime", "cutadapt", "trim-single", "--i-demultiplexed-sequences", demux_file, "--o-trimmed-sequences", str(output), "--p-cores", str(os.cpu_count() or 4), "--p-cut", str(self.config.trim_left_f)]
            if self.options.primer_f:
                command.extend(["--p-front", self.options.primer_f])
        self.runner.run(command, "cutadapt.log")
        self.runner.run(["qiime", "demux", "summarize", "--i-data", str(output), "--o-visualization", str(self.output_dir / "02_quality_control" / "trimmed_summary.qzv")], "pipeline.log")
        if self.options.primer_metadata:
            print("ℹ️ 已读取 primer-metadata 参数；样本特异性引物需在 QIIME2/cutadapt 环境中按样本拆分后处理。")
        return str(output)

    def _filter_low_quality(self, demux_file: str) -> str:
        output = self.output_dir / "01_demultiplexed" / "filtered.qza"
        command = ["qiime", "quality-filter", "q-score", "--i-demux", demux_file, "--o-filtered-sequences", str(output), "--o-filter-stats", str(self.output_dir / "logs" / "filter-stats.qza"), "--p-min-quality", str(self.config.min_quality)]
        self.runner.run(command, "pipeline.log")
        self.runner.run(["qiime", "demux", "summarize", "--i-data", str(output), "--o-visualization", str(self.output_dir / "02_quality_control" / "filtered_summary.qzv")], "pipeline.log")
        return str(output)

    def _run_figaro(self, demux_file: str) -> dict | None:
        figaro_available = self.runner.probe(["figaro", "--version"]) if self.options.qiime_env else shutil.which("figaro") is not None
        if not figaro_available and not self.options.dry_run:
            print("⚠️ 未找到 Figaro，继续使用 DADA2 默认截断长度。")
            return None
        figaro_dir = self.output_dir / "02_quality_control" / "figaro"
        figaro_dir.mkdir(parents=True, exist_ok=True)
        summary = figaro_dir / "input.qzv"
        self.runner.run(["qiime", "demux", "summarize", "--i-data", demux_file, "--o-visualization", str(summary)], "pipeline.log")
        command = ["figaro", "-i", str(summary), "-o", str(figaro_dir), "-a", "100"]
        if self.options.paired_end:
            command.append("-d")
        self.runner.run(command, "figaro.log")
        result = figaro_dir / "truncation.json"
        if not result.exists():
            return None
        return json.loads(result.read_text(encoding="utf-8"))

    def _run_dada2(self, demux_file: str, figaro_data: dict | None) -> tuple[str, str]:
        rep_seqs = self.output_dir / "03_denoised" / "rep-seqs.qza"
        table = self.output_dir / "03_denoised" / "feature-table.qza"
        stats = self.output_dir / "03_denoised" / "denoising-stats.qza"
        forward = figaro_data.get("truncLenForward", self.config.trunc_len_f) if figaro_data and self.options.paired_end else figaro_data.get("truncLen", self.config.trunc_len_f) if figaro_data else self.config.trunc_len_f
        reverse = figaro_data.get("truncLenReverse", self.config.trunc_len_r) if figaro_data and self.options.paired_end else self.config.trunc_len_r
        if self.options.paired_end:
            command = ["qiime", "dada2", "denoise-paired", "--i-demultiplexed-seqs", demux_file, "--p-trim-left-f", str(self.config.trim_left_f), "--p-trim-left-r", str(self.config.trim_left_r), "--p-trunc-len-f", str(forward), "--p-trunc-len-r", str(reverse)]
        else:
            command = ["qiime", "dada2", "denoise-single", "--i-demultiplexed-seqs", demux_file, "--p-trim-left", str(self.config.trim_left_f), "--p-trunc-len", str(forward)]
        command.extend(["--o-table", str(table), "--o-representative-sequences", str(rep_seqs), "--o-denoising-stats", str(stats), "--p-n-threads", str(os.cpu_count() or 4)])
        if self.config.max_ee is not None:
            command.extend(["--p-max-ee", str(self.config.max_ee)])
        if self.config.trunc_q is not None:
            command.extend(["--p-trunc-q", str(self.config.trunc_q)])
        self.runner.run(command, "pipeline.log")
        self.runner.run(["qiime", "metadata", "tabulate", "--m-input-file", str(stats), "--o-visualization", str(self.output_dir / "07_visualization" / "denoising-stats.qzv")], "pipeline.log")
        self.runner.run(["qiime", "feature-table", "summarize", "--i-table", str(table), "--o-visualization", str(self.output_dir / "02_quality_control" / "feature-table-summary.qzv")], "pipeline.log")
        filtered = self.output_dir / "03_denoised" / "filtered-table.qza"
        self.runner.run(["qiime", "feature-table", "filter-features", "--i-table", str(table), "--p-min-frequency", str(self.config.min_frequency), "--o-filtered-table", str(filtered)], "pipeline.log")
        table = filtered
        return str(table), str(rep_seqs)

    def _calculate_sampling_depth(self, table_qza: str) -> dict | None:
        if self.config.sampling_depth != "auto":
            return {"final_depth": int(self.config.sampling_depth), "source": "user"}
        if self.options.dry_run:
            return {"final_depth": self.config.min_absolute_depth, "source": "dry-run"}
        try:
            import biom  # optional dependency
        except ImportError:
            print("⚠️ 未安装 biom，无法自动计算采样深度；请显式传入 --sampling-depth。")
            return None
        export_dir = self.output_dir / "temp_export"
        try:
            self.runner.run(["qiime", "tools", "export", "--input-path", table_qza, "--output-path", str(export_dir)], "pipeline.log")
            table = biom.load_table(str(export_dir / "feature-table.biom"))
            depths = sorted(int(value) for value in table.sum(axis="sample"))
            if not depths:
                return None
            median = int(sorted(depths)[len(depths) // 2])
            retain_index = min(len(depths) - 1, int(len(depths) * (1 - self.config.min_sample_retain)))
            candidate = min(depths[retain_index], int(median * self.config.min_depth_percent))
            final_depth = max(candidate, self.config.min_absolute_depth)
            final_depth = min(final_depth, max(depths))
            return {"min_depth": min(depths), "max_depth": max(depths), "median_depth": median, "final_depth": final_depth, "samples_retained": sum(value >= final_depth for value in depths), "total_samples": len(depths), "source": "auto"}
        finally:
            if export_dir.exists() and not self.options.dry_run:
                shutil.rmtree(export_dir)

    def _group_column(self) -> str:
        capabilities = inspect_metadata_capabilities(self.config.metadata)
        return str(capabilities["group_column"]) if capabilities["group_ready"] else ""

    def _run_taxonomy(self, rep_seqs: str, table: str) -> str | None:
        taxonomy = self.output_dir / "04_taxonomy" / "taxonomy.qza"
        self.runner.run(["qiime", "feature-classifier", "classify-sklearn", "--i-classifier", self.config.classifier, "--i-reads", rep_seqs, "--o-classification", str(taxonomy), "--p-n-jobs", str(os.cpu_count() or 4)], "taxonomy.log")
        barplot = ["qiime", "taxa", "barplot", "--i-table", table, "--i-taxonomy", str(taxonomy)]
        if Path(self.config.metadata).is_file():
            barplot.extend(["--m-metadata-file", self.config.metadata])
        barplot.extend(["--o-visualization", str(self.output_dir / "07_visualization" / "taxonomy-barplot.qzv")])
        self.runner.run(barplot, "taxonomy.log")
        genus_table = self.output_dir / "04_taxonomy" / "genus-table.qza"
        self.runner.run(["qiime", "taxa", "collapse", "--i-table", table, "--i-taxonomy", str(taxonomy), "--p-level", "6", "--o-collapsed-table", str(genus_table)], "taxonomy.log")
        group_column = self._group_column()
        if group_column:
            self.runner.run(["qiime", "feature-table", "heatmap", "--i-table", str(genus_table), "--m-sample-metadata-file", self.config.metadata, "--m-sample-metadata-column", group_column, "--o-visualization", str(self.output_dir / "07_visualization" / "taxonomy-heatmap.qzv")], "taxonomy.log")
        else:
            print("⚠️ 没有可用分组列，已跳过 taxonomy heatmap。")
        return str(taxonomy)

    def _run_diversity(self, table: str, rep_seqs: str, depth_info: dict | None) -> str | None:
        depth = int(self.config.sampling_depth) if self.config.sampling_depth != "auto" else int((depth_info or {}).get("final_depth", 0))
        if depth <= 0:
            raise ValueError("无法确定采样深度，请使用 --sampling-depth 指定正整数")
        aligned = self.output_dir / "06_phylogeny" / "aligned-rep-seqs.qza"
        masked = self.output_dir / "06_phylogeny" / "masked-aligned-rep-seqs.qza"
        unrooted = self.output_dir / "06_phylogeny" / "unrooted-tree.qza"
        rooted = self.output_dir / "06_phylogeny" / "rooted-tree.qza"
        self.runner.run(["qiime", "alignment", "mafft", "--i-sequences", rep_seqs, "--o-alignment", str(aligned)], "diversity.log")
        self.runner.run(["qiime", "alignment", "mask", "--i-alignment", str(aligned), "--o-masked-alignment", str(masked)], "diversity.log")
        self.runner.run(["qiime", "phylogeny", "fasttree", "--i-alignment", str(masked), "--o-tree", str(unrooted)], "diversity.log")
        self.runner.run(["qiime", "phylogeny", "midpoint-root", "--i-tree", str(unrooted), "--o-rooted-tree", str(rooted)], "diversity.log")
        diversity_dir = self.output_dir / "05_diversity" / "core-metrics"
        if diversity_dir.exists() and not self.options.dry_run:
            shutil.rmtree(diversity_dir)
        self.runner.run(["qiime", "diversity", "core-metrics-phylogenetic", "--i-phylogeny", str(rooted), "--i-table", table, "--p-sampling-depth", str(depth), "--m-metadata-file", self.config.metadata, "--output-dir", str(diversity_dir)], "diversity.log")
        return str(diversity_dir)

    def _run_ancom(self, table: str, taxonomy: str) -> str | None:
        directory = self.output_dir / "09_differential_abundance"
        collapsed = directory / "genus-table.qza"
        composition = directory / "composition-table.qza"
        visualization = self.output_dir / "07_visualization" / "ancom-results.qzv"
        self.runner.run(["qiime", "taxa", "collapse", "--i-table", table, "--i-taxonomy", taxonomy, "--p-level", "6", "--o-collapsed-table", str(collapsed)], "ancom.log")
        self.runner.run(["qiime", "composition", "add-pseudocount", "--i-table", str(collapsed), "--o-composition-table", str(composition)], "ancom.log")
        self.runner.run(["qiime", "composition", "ancom", "--i-table", str(composition), "--m-metadata-file", self.config.metadata, "--m-metadata-column", self._group_column(), "--o-visualization", str(visualization)], "ancom.log")
        self.runner.run(["qiime", "tools", "export", "--input-path", str(visualization), "--output-path", str(directory)], "ancom.log")
        return str(visualization)

    def _visualize_phylogeny(self, rooted_tree: Path) -> str | None:
        if not rooted_tree.exists() and self.options.dry_run:
            return None
        target_dir = self.output_dir / "06_phylogeny"
        self.runner.run(["qiime", "tools", "export", "--input-path", str(rooted_tree), "--output-path", str(target_dir)], "pipeline.log")
        exported = target_dir / "tree.nwk"
        target = target_dir / "rooted-tree.nwk"
        if exported.exists() and exported != target:
            exported.replace(target)
        return str(target) if target.exists() else None


def run_analysis(config: AnalysisConfig, options: PipelineOptions) -> PipelineResult:
    return PipelineService(config, options).run()
