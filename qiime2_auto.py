#!/usr/bin/env python
"""QIIME2 Auto：16S 分析的命令行入口。

这个文件刻意保持轻量：即使没有安装 QIIME2、biom 或 pandas，也可以运行
`--help`、`scan`、manifest/metadata 模板功能和 `serve` 本地控制台。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qiime2auto import __version__
from qiime2auto.config import AnalysisConfig, ConfigError, DEFAULT_CONFIG
from qiime2auto.io import (
    create_output_structure,
    detect_data_type,
    generate_manifest,
    generate_metadata_template,
    read_sample_ids,
    scan_input,
    validate_metadata_details,
)
from qiime2auto.pipeline import PipelineOptions, run_analysis
from qiime2auto.environment import discover_environments, install_command, install_options


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="QIIME2 Auto：面向新手的 16S rRNA 分析助手（CLI + 本地 Web 控制台）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-i", "--input", help="输入文件或目录")
    parser.add_argument("-o", "--output", default="qiime2_analysis", help="输出目录")
    parser.add_argument("--barcodes", help="混样数据的 barcode metadata 文件")
    parser.add_argument("--generate-manifest", action="store_true", help="扫描输入目录并生成 manifest.tsv")
    parser.add_argument("--paired-end", action="store_true", help="生成 manifest 时按双端数据处理")
    parser.add_argument("--single-end", action="store_true", help="生成 manifest 时按单端数据处理")
    parser.add_argument("--generate-metadata", action="store_true", help="根据 manifest 生成 metadata.tsv 模板")
    parser.add_argument("--metadata-columns", nargs="+", default=["group"], help="metadata 模板中的附加列")
    parser.add_argument("--primer-f", help="正向引物序列")
    parser.add_argument("--primer-r", help="反向引物序列")
    parser.add_argument("--primer-metadata", help="样本特异性引物 metadata 文件")
    parser.add_argument("--primer-f-col", default="primer_f", help="样本特异性正向引物列名")
    parser.add_argument("--primer-r-col", default="primer_r", help="样本特异性反向引物列名")
    parser.add_argument("--phred-offset", type=int, choices=[33, 64], default=DEFAULT_CONFIG["phred_offset"], help="质量分数偏移值")
    parser.add_argument("--barcode-length", type=int, default=DEFAULT_CONFIG["barcode_length"], help="barcode 长度")
    parser.add_argument("--min-quality", type=int, default=DEFAULT_CONFIG["min_quality"], help="质量过滤阈值")
    parser.add_argument("--min-frequency", type=int, default=DEFAULT_CONFIG["min_frequency"], help="特征最小频率")
    parser.add_argument("--trim-left-f", type=int, default=DEFAULT_CONFIG["trim_left_f"], help="正向读段左侧截断碱基数")
    parser.add_argument("--trim-left-r", type=int, default=DEFAULT_CONFIG["trim_left_r"], help="反向读段左侧截断碱基数")
    parser.add_argument("--trunc-len-f", type=int, default=DEFAULT_CONFIG["trunc_len_f"], help="正向读段截断长度")
    parser.add_argument("--trunc-len-r", type=int, default=DEFAULT_CONFIG["trunc_len_r"], help="反向读段截断长度")
    parser.add_argument("--max-ee", type=float, help="最大预期错误值")
    parser.add_argument("--trunc-q", type=int, help="质量截断阈值")
    parser.add_argument("--sampling-depth", default=DEFAULT_CONFIG["sampling_depth"], help="采样深度：正整数或 auto")
    parser.add_argument("--min-sample-retain", type=float, default=DEFAULT_CONFIG["min_sample_retain"], help="自动采样深度时的最小样本保留比例")
    parser.add_argument("--min-depth-percent", type=float, default=DEFAULT_CONFIG["min_depth_percent"], help="自动采样深度相对中位数比例")
    parser.add_argument("--min-absolute-depth", type=int, default=DEFAULT_CONFIG["min_absolute_depth"], help="自动采样深度的绝对下限")
    parser.add_argument("--classifier", default=DEFAULT_CONFIG["classifier"], help="QIIME2 sklearn 分类器 .qza 路径")
    parser.add_argument("--metadata", default=DEFAULT_CONFIG["metadata"], help="样本 metadata.tsv 路径")
    parser.add_argument("--no-trim", action="store_true", help="跳过引物去除")
    parser.add_argument("--no-filter", action="store_true", help="跳过低质量过滤")
    parser.add_argument("--no-figaro", action="store_true", help="跳过 Figaro 截断优化")
    parser.add_argument("--skip-taxonomy", action="store_true", help="跳过物种分类")
    parser.add_argument("--skip-diversity", action="store_true", help="跳过多样性分析")
    parser.add_argument("--skip-ancom", action="store_true", help="跳过 ANCOM")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不调用外部工具")
    parser.add_argument("--qiime-env", help="运行 QIIME2 的 Conda 环境名")
    parser.add_argument("--version", action="version", version=f"QIIME2 Auto {__version__}")
    return parser


def build_config(args: argparse.Namespace) -> AnalysisConfig:
    return AnalysisConfig.from_mapping({
        "trim_left_f": args.trim_left_f,
        "trim_left_r": args.trim_left_r,
        "trunc_len_f": args.trunc_len_f,
        "trunc_len_r": args.trunc_len_r,
        "classifier": args.classifier,
        "sampling_depth": args.sampling_depth,
        "min_sample_retain": args.min_sample_retain,
        "min_depth_percent": args.min_depth_percent,
        "min_absolute_depth": args.min_absolute_depth,
        "metadata": args.metadata,
        "phred_offset": args.phred_offset,
        "barcode_length": args.barcode_length,
        "min_quality": args.min_quality,
        "min_frequency": args.min_frequency,
        "max_ee": args.max_ee,
        "trunc_q": args.trunc_q,
    })


def run_legacy_cli(args: argparse.Namespace) -> int:
    if not args.input:
        print("❌ 缺少 --input。先运行 `python qiime2_auto.py --help` 查看示例。", file=sys.stderr)
        return 2
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ 输入不存在: {input_path}", file=sys.stderr)
        return 2
    output_dir = Path(args.output)

    if args.generate_manifest:
        if args.paired_end == args.single_end:
            print("❌ 生成 manifest 时请二选一：--paired-end 或 --single-end", file=sys.stderr)
            return 2
        result = generate_manifest(input_path, output_dir / "manifest.tsv", paired_end=args.paired_end)
        if not result:
            print("❌ 没有找到可配对的 FASTQ 文件", file=sys.stderr)
            return 1
        print(f"✅ Manifest 已生成: {result}")
        return 0

    if args.generate_metadata:
        try:
            result = generate_metadata_template(read_sample_ids(input_path), output_dir / "metadata.tsv", args.metadata_columns)
        except (OSError, ValueError) as exc:
            print(f"❌ 生成 metadata 失败: {exc}", file=sys.stderr)
            return 1
        print(f"✅ Metadata 模板已生成: {result}")
        return 0

    metadata_result = validate_metadata_details(args.metadata)
    if not metadata_result.valid:
        print("❌ metadata 校验失败:")
        for error in metadata_result.errors or []:
            print(f"  - {error}")
        print("提示：可以先用 `--generate-metadata` 生成模板，再补齐分组信息。")
        return 2

    data_type = detect_data_type(input_path)
    if not data_type:
        print("❌ 无法识别输入格式。支持 EMP、Casava、manifest 和混样 FASTQ。", file=sys.stderr)
        return 2
    if "muxed" in data_type and not args.barcodes:
        print("❌ 混样数据必须提供 --barcodes", file=sys.stderr)
        return 2
    try:
        config = build_config(args)
    except ConfigError as exc:
        print(f"❌ 参数校验失败: {exc}", file=sys.stderr)
        return 2
    options = PipelineOptions(
        input_path=str(input_path), output_dir=str(output_dir), data_type=data_type,
        barcodes=args.barcodes, primer_f=args.primer_f, primer_r=args.primer_r,
        primer_metadata=args.primer_metadata, primer_f_col=args.primer_f_col,
        primer_r_col=args.primer_r_col, no_trim=args.no_trim, no_filter=args.no_filter,
        no_figaro=args.no_figaro, skip_taxonomy=args.skip_taxonomy,
        skip_diversity=args.skip_diversity, skip_ancom=args.skip_ancom, dry_run=args.dry_run,
        qiime_env=args.qiime_env,
    )
    result = run_analysis(config, options)
    if result.success:
        print(f"\n✅ 分析完成：{result.output_dir}")
        print(f"📝 报告：{result.report}")
        return 0
    print(f"\n❌ 分析未完成：{result.error}", file=sys.stderr)
    return 1


def run_scan(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="扫描输入并输出 JSON")
    parser.add_argument("path")
    args = parser.parse_args(argv)
    print(json.dumps(scan_input(args.path), ensure_ascii=False, indent=2))
    return 0


def run_serve(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="启动本地 Web 控制台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    from qiime2auto.web import serve

    serve(args.host, args.port)
    return 0


def run_envs(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="查询 Conda 环境与其中的 QIIME2")
    parser.add_argument("--no-probe", action="store_true", help="只执行 conda env list，不探测 qiime")
    args = parser.parse_args(argv)
    print(json.dumps(discover_environments(probe=not args.no_probe), ensure_ascii=False, indent=2))
    return 0


def run_install(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="生成 QIIME2 Linux Conda 安装命令")
    parser.add_argument("--version", choices=install_options()["versions"], required=True)
    parser.add_argument("--distribution", choices=install_options()["distributions"], default="amplicon")
    parser.add_argument("--name")
    args = parser.parse_args(argv)
    print(" ".join(install_command(args.version, args.distribution, args.name)))
    return 0


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "scan":
        return run_scan(values[1:])
    if values and values[0] == "serve":
        return run_serve(values[1:])
    if values and values[0] == "envs":
        return run_envs(values[1:])
    if values and values[0] == "install":
        return run_install(values[1:])
    return run_legacy_cli(build_parser().parse_args(values))


if __name__ == "__main__":
    raise SystemExit(main())
