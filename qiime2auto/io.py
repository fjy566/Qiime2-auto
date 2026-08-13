"""输入扫描、manifest、metadata 和报告文件处理。"""

from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")
_READ_RE = re.compile(r"(?P<sample>.+?)(?:_S\d+)?_L\d+_R(?P<read>[12])_\d+", re.IGNORECASE)
_GENERIC_READ_RE = re.compile(r"(?P<sample>.+?)_R(?P<read>[12])(?:_|\.|$)", re.IGNORECASE)


@dataclass
class ValidationResult:
    valid: bool
    path: str
    sample_count: int = 0
    columns: list[str] | None = None
    errors: list[str] | None = None
    warnings: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def is_fastq(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in FASTQ_SUFFIXES)


def _sample_and_read(path: Path) -> tuple[str, str] | None:
    stem = path.name
    for suffix in FASTQ_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    match = _READ_RE.search(stem) or _GENERIC_READ_RE.search(stem)
    if match:
        return match.group("sample"), f"R{match.group('read')}"
    return None


def _sample_id_for_single(path: Path) -> str:
    stem = path.name
    for suffix in FASTQ_SUFFIXES:
        if stem.lower().endswith(suffix):
            return stem[: -len(suffix)]
    return path.stem


def generate_manifest(input_dir: str | Path, output_path: str | Path, paired_end: bool = True) -> str | None:
    """从 FASTQ 文件名生成 QIIME2 manifest，返回文件路径或 None。"""
    source = Path(input_dir)
    if not source.is_dir():
        raise ValueError(f"输入目录不存在: {source}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted((path for path in source.rglob("*") if path.is_file() and is_fastq(path)), key=lambda p: str(p).lower())
    if not files:
        return None

    samples: dict[str, dict[str, Path]] = {}
    for path in files:
        parsed = _sample_and_read(path)
        if paired_end:
            if parsed:
                sample_id, read = parsed
                samples.setdefault(sample_id, {})[read] = path.resolve()
        else:
            if parsed and parsed[1] == "R2":
                continue
            samples.setdefault(_sample_id_for_single(path), {})["R1"] = path.resolve()

    rows: list[tuple[str, str, str | None]] = []
    for sample_id, paths in sorted(samples.items()):
        if paired_end and not (paths.get("R1") and paths.get("R2")):
            continue
        rows.append((sample_id, str(paths["R1"]), str(paths.get("R2")) if paired_end else None))
    if not rows:
        return None

    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        if paired_end:
            writer.writerow(["sample-id", "forward-absolute-filepath", "reverse-absolute-filepath"])
            writer.writerows(rows)
        else:
            writer.writerow(["sample-id", "absolute-filepath"])
            writer.writerows((sample_id, forward) for sample_id, forward, _ in rows)
    return str(output.resolve())


def generate_metadata_template(sample_ids: Iterable[str], output_path: str | Path, additional_columns: Iterable[str] | None = None) -> str:
    columns = [column.strip() for column in (additional_columns or ["group"]) if column.strip()]
    if not columns or any(column == "sample-id" for column in columns) or len(set(columns)) != len(columns):
        raise ValueError("metadata 列名必须非空、唯一，且不能重复 sample-id")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["sample-id", *columns])
        for sample_id in sorted({str(value).strip() for value in sample_ids if str(value).strip()}):
            writer.writerow([sample_id, *(f"{sample_id}_{column}" for column in columns)])
    return str(output.resolve())


def _read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        lines = []
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lstrip("#").lower().startswith(("sample-id\t", "sampleid\t")):
                lines.append(line.lstrip("#"))
            elif not line.lstrip().startswith("#"):
                lines.append(line)
    if not lines:
        return [], []
    first = lines[0]
    if first.lstrip().lower().startswith("#sample-id"):
        lines[0] = first.lstrip()[1:]
    reader = csv.DictReader(lines, delimiter="\t")
    headers = [str(value).strip() for value in (reader.fieldnames or [])]
    headers = ["sample-id" if value.lower().replace("_", "-") in {"sampleid", "sample-id"} else value for value in headers]
    reader.fieldnames = headers
    rows = [{str(key).strip(): (value or "").strip() for key, value in row.items() if key is not None} for row in reader]
    return headers, rows


def validate_metadata_details(metadata_path: str | Path) -> ValidationResult:
    path = Path(metadata_path)
    result = ValidationResult(valid=False, path=str(path.resolve()), columns=[], errors=[], warnings=[])
    if not path.is_file():
        result.errors.append("文件不存在")
        return result
    try:
        headers, rows = _read_table(path)
    except (OSError, UnicodeError, csv.Error) as exc:
        result.errors.append(f"无法读取文件: {exc}")
        return result
    result.columns = headers
    result.sample_count = len(rows)
    if "sample-id" not in headers:
        result.errors.append("缺少 sample-id 列")
    if len(headers) != len(set(headers)):
        result.errors.append("列名重复")
    if not rows:
        result.errors.append("没有可用样本行")
    sample_ids = [row.get("sample-id", "") for row in rows]
    if any(not value for value in sample_ids):
        result.errors.append("sample-id 不能为空")
    duplicates = sorted({value for value in sample_ids if value and sample_ids.count(value) > 1})
    if duplicates:
        result.errors.append(f"样本 ID 重复: {', '.join(duplicates)}")
    if any(not value for row in rows for value in row.values()):
        result.errors.append("表格包含空值")
    if len(headers) < 2:
        result.warnings.append("只有 sample-id，没有分组列；多样性与差异分析可能无法运行")
    result.valid = not result.errors
    return result


def validate_metadata(metadata_path: str | Path) -> bool:
    """兼容旧 API：只返回布尔值。"""
    return validate_metadata_details(metadata_path).valid


def read_sample_ids(path: str | Path) -> list[str]:
    headers, rows = _read_table(Path(path))
    if "sample-id" not in headers:
        raise ValueError("文件缺少 sample-id 列")
    return [row["sample-id"] for row in rows if row.get("sample-id")]


def detect_data_type(input_path: str | Path) -> str | None:
    path = Path(input_path)
    if path.is_dir():
        names = {file.name for file in path.iterdir() if file.is_file()}
        if {"barcodes.fastq.gz", "sequences.fastq.gz"}.issubset(names):
            return "EMP_single"
        if {"barcodes.fastq.gz", "forward.fastq.gz", "reverse.fastq.gz"}.issubset(names):
            return "EMP_paired"
        fastq_names = [name for name in names if is_fastq(Path(name))]
        has_r1 = any(_sample_and_read(Path(name)) and _sample_and_read(Path(name))[1] == "R1" for name in fastq_names)
        has_r2 = any(_sample_and_read(Path(name)) and _sample_and_read(Path(name))[1] == "R2" for name in fastq_names)
        if has_r1 or has_r2:
            return "Casava_paired" if has_r1 and has_r2 else "Casava_single"
    elif path.is_file() and path.suffix.lower() in {".tsv", ".csv", ".txt"}:
        try:
            header = path.read_text(encoding="utf-8-sig").splitlines()[0].lstrip("#").lower()
        except (OSError, IndexError, UnicodeError):
            return None
        if "sample-id" in header and "reverse-absolute-filepath" in header:
            return "manifest_paired"
        if "sample-id" in header and ("absolute-filepath" in header or "forward-absolute-filepath" in header):
            return "manifest_single"
    elif path.is_file() and is_fastq(path):
        return "muxed_paired" if "_r1" in path.name.lower() else "muxed_single"
    return None


def scan_input(input_path: str | Path) -> dict:
    path = Path(input_path)
    data_type = detect_data_type(path)
    fastq_files = sorted(str(file) for file in path.rglob("*") if file.is_file() and is_fastq(file)) if path.is_dir() else []
    return {
        "path": str(path.resolve()),
        "exists": path.exists(),
        "kind": "directory" if path.is_dir() else "file" if path.is_file() else "missing",
        "data_type": data_type,
        "paired_end": bool(data_type and "paired" in data_type),
        "fastq_count": len(fastq_files),
        "fastq_files": fastq_files[:100],
        "warnings": [] if data_type else ["无法识别输入格式，请检查命名或准备 manifest.tsv"],
    }


def create_output_structure(output_dir: str | Path) -> str:
    root = Path(output_dir).resolve()
    for name in ("00_raw_data", "01_demultiplexed", "02_quality_control", "03_denoised", "04_taxonomy", "05_diversity", "06_phylogeny", "07_visualization", "08_rarefaction_curves", "09_differential_abundance", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return str(root)


def collect_result_files(output_dir: str | Path) -> dict[str, list[dict[str, str]]]:
    root = Path(output_dir)
    categories: dict[str, list[dict[str, str]]] = {}
    for file in sorted(root.rglob("*")):
        if not file.is_file() or file.name == "analysis_report.md":
            continue
        relative = file.relative_to(root)
        category = relative.parts[0] if relative.parts else "root"
        categories.setdefault(category, []).append({"name": str(relative).replace("\\", "/"), "desc": file.suffix.lstrip(".").upper() or "结果文件"})
    return categories


def generate_report(output_dir: str | Path, params: dict, sampling_depth_info: dict | None = None) -> str:
    root = Path(output_dir)
    report_path = root / "analysis_report.md"
    with report_path.open("w", encoding="utf-8") as handle:
        handle.write("# QIIME2 Auto 16S 分析报告\n\n")
        handle.write(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        handle.write("## 分析参数\n\n| 参数 | 值 |\n| --- | --- |\n")
        for key, value in params.items():
            handle.write(f"| {key} | {str(value).replace('|', '\\|')} |\n")
        if sampling_depth_info:
            handle.write("\n## 采样深度\n\n")
            for key, value in sampling_depth_info.items():
                handle.write(f"- **{key}**：{value}\n")
        handle.write("\n## 实际生成的结果文件\n\n")
        handle.write("| 目录 | 文件 | 类型 |\n| --- | --- | --- |\n")
        for category, files in collect_result_files(root).items():
            for item in files:
                handle.write(f"| {category} | `{item['name']}` | {item['desc']} |\n")
        handle.write("\n## 使用提示\n\n")
        handle.write("`.qzv` 文件可上传到 [QIIME2 View](https://view.qiime2.org) 查看；请同时保存本报告和 `logs/` 目录。\n")
    return str(report_path)
