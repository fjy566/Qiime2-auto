"""输入扫描、manifest、metadata 和报告文件处理。"""

from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .table_editor import read_table as _read_editable_table
from .table_editor import validate_metadata_payload


FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")
_READ_RE = re.compile(r"(?P<sample>.+?)(?:_S\d+)?_L\d+_R(?P<read>[12])_\d+", re.IGNORECASE)
_GENERIC_READ_RE = re.compile(r"(?P<sample>.+?)_R(?P<read>[12])(?:_|\.|$)", re.IGNORECASE)
_NUMERIC_READ_RE = re.compile(r"(?P<sample>.+?)[_-](?P<read>[12])(?:[_-]\d+)?$", re.IGNORECASE)

MANIFEST_SINGLE_COLUMNS = {"absolute-filepath", "forward-absolute-filepath"}
MANIFEST_PAIRED_COLUMNS = {"forward-absolute-filepath", "reverse-absolute-filepath"}


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
    match = _READ_RE.search(stem) or _GENERIC_READ_RE.search(stem) or _NUMERIC_READ_RE.search(stem)
    if match:
        return match.group("sample"), f"R{match.group('read')}"
    return None


def _sample_id_for_single(path: Path) -> str:
    stem = path.name
    for suffix in FASTQ_SUFFIXES:
        if stem.lower().endswith(suffix):
            return stem[: -len(suffix)]
    return path.stem


def _manifest_type(headers: Iterable[str]) -> str | None:
    normalized = {str(header).strip().lower() for header in headers}
    if "sample-id" not in normalized and "sampleid" not in normalized:
        return None
    if MANIFEST_PAIRED_COLUMNS.issubset(normalized):
        return "manifest_paired"
    if normalized & MANIFEST_SINGLE_COLUMNS:
        return "manifest_single"
    return None


def _manifest_columns(headers: Iterable[str], data_type: str) -> tuple[str, ...]:
    normalized = {str(header).strip().lower(): str(header).strip() for header in headers}
    if data_type == "manifest_paired":
        return (normalized["sample-id"], normalized["forward-absolute-filepath"], normalized["reverse-absolute-filepath"])
    filepath_column = "absolute-filepath" if "absolute-filepath" in normalized else "forward-absolute-filepath"
    return (normalized["sample-id"], normalized[filepath_column])


def _resolve_manifest_reference(reference: str, manifest_path: Path, bundle_dir: Path | None = None) -> Path | None:
    value = str(reference or "").strip()
    if not value:
        return None
    value = value.removeprefix("file://")
    raw = Path(value)
    candidates: list[Path] = []
    if raw.is_absolute() or re.match(r"^[A-Za-z]:[\\/].*", value):
        candidates.append(raw)
    else:
        candidates.extend((manifest_path.parent / raw, manifest_path.parent / raw.name))
    if bundle_dir:
        candidates.extend((bundle_dir / raw, bundle_dir / raw.name))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def inspect_manifest(manifest_path: str | Path, bundle_dir: str | Path | None = None) -> dict:
    """读取 manifest 内容，统计引用的 FASTQ，而不是只看 manifest 自身。"""
    path = Path(manifest_path)
    try:
        headers, rows = _read_table(path)
    except (OSError, UnicodeError, csv.Error):
        return {"data_type": None, "headers": [], "sample_count": 0, "fastq_files": [], "missing_files": [], "errors": ["无法读取 manifest 内容"]}
    data_type = _manifest_type(headers)
    if not data_type:
        return {"data_type": None, "headers": headers, "sample_count": len(rows), "fastq_files": [], "missing_files": [], "errors": ["manifest 缺少 sample-id 与 filepath 列"]}
    columns = _manifest_columns(headers, data_type)
    root = Path(bundle_dir) if bundle_dir else None
    references: list[str] = []
    resolved_files: list[str] = []
    missing_files: list[str] = []
    for row in rows:
        for column in columns[1:]:
            reference = str(row.get(column, "")).strip()
            if not reference:
                continue
            references.append(reference)
            resolved = _resolve_manifest_reference(reference, path, root)
            if resolved:
                resolved_files.append(str(resolved))
            else:
                missing_files.append(reference)
    warnings = []
    if missing_files:
        warnings.append(f"有 {len(missing_files)} 个序列文件暂时找不到；如果是 picker 选择的文件，请继续选择 FASTQ，程序会自动合并。")
    if not rows:
        warnings.append("manifest 没有样本行。")
    return {
        "data_type": data_type,
        "headers": headers,
        "sample_count": len(rows),
        "fastq_count": len(references),
        "fastq_files": references,
        "resolved_files": resolved_files,
        "missing_files": missing_files,
        "warnings": warnings,
    }


def reconcile_manifest(manifest_path: str | Path, bundle_dir: str | Path) -> str:
    """将上传包中的 manifest 路径统一到当前服务器文件，避免跨机器路径失效。"""
    path = Path(manifest_path)
    bundle = Path(bundle_dir)
    headers, rows = _read_table(path)
    data_type = _manifest_type(headers)
    if not data_type:
        return str(path.resolve())
    columns = _manifest_columns(headers, data_type)
    output = bundle / ".qiime2auto_manifest.tsv"
    normalized_rows: list[list[str]] = []
    for row in rows:
        values = [str(row.get(columns[0], "")).strip()]
        for column in columns[1:]:
            reference = str(row.get(column, "")).strip()
            resolved = _resolve_manifest_reference(reference, path, bundle)
            if not resolved:
                return str(path.resolve())
            values.append(str(resolved))
        normalized_rows.append(values)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        if data_type == "manifest_paired":
            writer.writerow(["sample-id", "forward-absolute-filepath", "reverse-absolute-filepath"])
        else:
            writer.writerow(["sample-id", "absolute-filepath"])
        writer.writerows(normalized_rows)
    return str(output.resolve())


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
        writer.writerow(["#q2:types", *("categorical" for _ in columns)])
        for sample_id in sorted({str(value).strip() for value in sample_ids if str(value).strip()}):
            writer.writerow([sample_id, *("" for _ in columns)])
    return str(output.resolve())


def _read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    table = _read_editable_table(path)
    return table["headers"], table["rows"]


def validate_metadata_details(metadata_path: str | Path) -> ValidationResult:
    path = Path(metadata_path)
    result = ValidationResult(valid=False, path=str(path.resolve()), columns=[], errors=[], warnings=[])
    if not path.is_file():
        result.errors.append("文件不存在")
        return result
    try:
        table = _read_editable_table(path)
        headers, rows = table["headers"], table["rows"]
        payload = validate_metadata_payload(headers, table["types"], rows)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        result.errors.append(f"无法读取文件: {exc}")
        return result
    result.columns = headers
    result.sample_count = len(rows)
    result.errors.extend(payload.get("errors", []))
    result.warnings.extend(payload.get("warnings", []))
    result.valid = bool(payload.get("valid"))
    return result


def validate_metadata(metadata_path: str | Path) -> bool:
    """兼容旧 API：只返回布尔值。"""
    return validate_metadata_details(metadata_path).valid


def read_sample_ids(path: str | Path) -> list[str]:
    headers, rows = _read_table(Path(path))
    if "sample-id" not in headers:
        raise ValueError("文件缺少 sample-id 列")
    return [row["sample-id"] for row in rows if row.get("sample-id")]


def sample_ids_for_input(input_path: str | Path) -> list[str]:
    """Return sample IDs that can be selected before a metadata file exists."""

    path = Path(input_path)
    scan = scan_input(path)
    manifest_path = scan.get("manifest_path")
    if manifest_path:
        return sorted(set(read_sample_ids(manifest_path)))
    if path.is_file() and str(scan.get("data_type", "")).startswith("manifest"):
        return sorted(set(read_sample_ids(path)))
    files = [path] if path.is_file() and is_fastq(path) else [item for item in path.rglob("*") if item.is_file() and is_fastq(item)] if path.is_dir() else []
    sample_ids = {parsed[0] for file in files if (parsed := _sample_and_read(file))}
    return sorted(sample_ids)


def detect_data_type(input_path: str | Path) -> str | None:
    path = Path(input_path)
    if path.is_dir():
        manifest_candidates: list[tuple[Path, str]] = []
        for file in path.rglob("*"):
            if not file.is_file() or is_fastq(file) or file.name.startswith("."):
                continue
            try:
                preview = file.read_text(encoding="utf-8-sig")[:4096].lower()
            except (OSError, UnicodeError):
                continue
            if "sample-id" not in preview or "filepath" not in preview:
                continue
            manifest_info = inspect_manifest(file)
            if manifest_info.get("data_type"):
                manifest_candidates.append((file, manifest_info["data_type"]))
        if manifest_candidates:
            return manifest_candidates[0][1]
        names = {file.name for file in path.iterdir() if file.is_file()}
        if {"barcodes.fastq.gz", "sequences.fastq.gz"}.issubset(names):
            return "EMP_single"
        if {"barcodes.fastq.gz", "forward.fastq.gz", "reverse.fastq.gz"}.issubset(names):
            return "EMP_paired"
        fastq_files = [file for file in path.rglob("*") if file.is_file() and is_fastq(file)]
        has_r1 = any(_sample_and_read(file) and _sample_and_read(file)[1] == "R1" for file in fastq_files)
        has_r2 = any(_sample_and_read(file) and _sample_and_read(file)[1] == "R2" for file in fastq_files)
        if has_r1 or has_r2:
            return "Casava_paired" if has_r1 and has_r2 else "Casava_single"
    elif path.is_file() and not is_fastq(path):
        return inspect_manifest(path).get("data_type")
    elif path.is_file() and is_fastq(path):
        return "muxed_paired" if "_r1" in path.name.lower() else "muxed_single"
    return None


def scan_input(input_path: str | Path, bundle_dir: str | Path | None = None) -> dict:
    path = Path(input_path)
    data_type = detect_data_type(path)
    manifest_path: Path | None = None
    manifest_info: dict = {}
    if path.is_file() and data_type and data_type.startswith("manifest"):
        manifest_path = path
        manifest_info = inspect_manifest(path, bundle_dir)
    elif path.is_dir() and data_type and data_type.startswith("manifest"):
        for candidate in sorted(path.rglob("*")):
            if candidate.is_file() and not is_fastq(candidate):
                info = inspect_manifest(candidate, bundle_dir)
                if info.get("data_type") == data_type:
                    manifest_path, manifest_info = candidate, info
                    break
    if manifest_info:
        fastq_files = manifest_info.get("fastq_files", [])
        analysis_path = str(manifest_path.resolve()) if manifest_path else str(path.resolve())
        warnings = list(manifest_info.get("warnings", []))
        sample_count = manifest_info.get("sample_count", 0)
    else:
        fastq_paths = sorted((file for file in path.rglob("*") if file.is_file() and is_fastq(file)), key=lambda value: str(value).lower()) if path.is_dir() else []
        fastq_files = [str(file.resolve()) for file in fastq_paths]
        analysis_path = str(path.resolve())
        warnings = [] if data_type else ["无法识别输入格式，请检查命名或准备 manifest.tsv"]
        sample_count = len({parsed[0] for file in fastq_paths if (parsed := _sample_and_read(file)) and parsed[1] == "R1"})
    if manifest_path:
        warnings = warnings or []
    return {
        "path": str(path.resolve()),
        "analysis_path": analysis_path,
        "exists": path.exists(),
        "kind": "directory" if path.is_dir() else "file" if path.is_file() else "missing",
        "data_type": data_type,
        "paired_end": bool(data_type and "paired" in data_type),
        "fastq_count": len(fastq_files),
        "existing_fastq_count": len(manifest_info.get("resolved_files", fastq_files)) if manifest_info else len(fastq_files),
        "missing_fastq_count": len(manifest_info.get("missing_files", [])) if manifest_info else 0,
        "fastq_files": fastq_files[:100],
        "sample_count": sample_count,
        "manifest_path": str(manifest_path.resolve()) if manifest_path else None,
        "manifest_headers": manifest_info.get("headers", []) if manifest_info else [],
        "warnings": warnings,
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
