"""Editable QIIME 2 metadata and manifest tables.

The web UI deliberately keeps these operations out of the request handler.  This
module owns the small amount of TSV knowledge needed by both the browser editor
and the command-line workflow.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable


METADATA_TYPES = ("", "categorical", "numeric")
MANIFEST_HEADERS = {
    "manifest_single": ("sample-id", "absolute-filepath"),
    "manifest_paired": ("sample-id", "forward-absolute-filepath", "reverse-absolute-filepath"),
}
_SAMPLE_ID_ALIASES = {"sample-id", "sampleid", "sample_id"}
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


class TableError(ValueError):
    """A user-editable table cannot be represented as a valid QIIME 2 table."""


def _clean_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if "\t" in text or "\r" in text or "\n" in text:
        raise TableError("单元格不能包含制表符或换行符")
    return text.strip()


def _canonical_header(value: object) -> str:
    header = _clean_cell(value)
    normalized = header.lower().replace("_", "-")
    if normalized in _SAMPLE_ID_ALIASES:
        return "sample-id"
    return header


def _normalise_types(headers: list[str], types: Iterable[object] | None) -> list[str]:
    values = ["" if value is None else str(value).strip().lower() for value in (types or [])]
    if len(values) < len(headers):
        values.extend([""] * (len(headers) - len(values)))
    values = values[: len(headers)]
    values[0] = ""
    invalid = sorted({value for value in values if value not in METADATA_TYPES})
    if invalid:
        raise TableError(f"类型只能是 categorical 或 numeric：{', '.join(invalid)}")
    return values


def read_table(path: str | Path) -> dict:
    """Read a QIIME-style tab-separated file and retain its type directive."""

    table_path = Path(path)
    with table_path.open("r", encoding="utf-8-sig", newline="") as handle:
        raw_lines = [line.rstrip("\r\n") for line in handle if line.strip()]
    if not raw_lines:
        return {"path": str(table_path.resolve()), "headers": [], "types": [], "rows": []}

    header_fields: list[str] | None = None
    type_values: list[str] | None = None
    data_lines: list[str] = []
    for line in raw_lines:
        first = line.split("\t", 1)[0].strip().lower()
        if first == "#q2:types":
            fields = next(csv.reader([line], delimiter="\t"))
            type_values = [str(value).strip().lower() for value in fields[1:]]
            continue
        if first.startswith("#") and first.lstrip("#").lower().split("\t", 1)[0] in _SAMPLE_ID_ALIASES:
            line = line.lstrip("#")
        elif line.lstrip().startswith("#"):
            continue
        if header_fields is None:
            header_fields = next(csv.reader([line], delimiter="\t"))
        else:
            data_lines.append(line)

    if not header_fields:
        return {"path": str(table_path.resolve()), "headers": [], "types": [], "rows": []}

    headers = [_canonical_header(value) for value in header_fields]
    types = [""] + (type_values or [])
    types = _normalise_types(headers, types)
    reader = csv.DictReader(["\t".join(headers), *data_lines], delimiter="\t")
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({header: _clean_cell(row.get(header, "")) for header in headers})
    return {"path": str(table_path.resolve()), "headers": headers, "types": types, "rows": rows}


def _validate_headers(headers: Iterable[object], minimum: int = 1) -> list[str]:
    cleaned = [_canonical_header(value) for value in headers]
    if len(cleaned) < minimum:
        raise TableError("表格列数不足")
    if any(not value for value in cleaned):
        raise TableError("列名不能为空")
    if len({value.lower() for value in cleaned}) != len(cleaned):
        raise TableError("列名不能重复")
    if cleaned[0] != "sample-id":
        raise TableError("第一列必须是 sample-id（也接受 #SampleID，保存时会统一成 sample-id）")
    return cleaned


def validate_metadata_payload(headers: Iterable[object], types: Iterable[object] | None, rows: Iterable[dict]) -> dict:
    """Validate editor payload using QIIME 2's missing-value semantics."""

    errors: list[str] = []
    warnings: list[str] = []
    try:
        cleaned_headers = _validate_headers(headers, minimum=1)
        cleaned_types = _normalise_types(cleaned_headers, types)
    except TableError as exc:
        return {"valid": False, "sample_count": 0, "columns": [], "types": [], "errors": [str(exc)], "warnings": []}

    normalised_rows: list[dict[str, str]] = []
    for index, raw_row in enumerate(rows, start=1):
        row = {header: _clean_cell(raw_row.get(header, "")) for header in cleaned_headers}
        normalised_rows.append(row)
        if not row["sample-id"]:
            errors.append(f"第 {index} 行的 sample-id 不能为空")
        for header, column_type in zip(cleaned_headers[1:], cleaned_types[1:]):
            value = row[header]
            if not value:
                warnings.append(f"第 {index} 行的 {header} 为空，将按 QIIME2 缺失值处理")
            elif column_type == "numeric" and not _NUMBER_RE.fullmatch(value):
                errors.append(f"第 {index} 行的 {header} 不是有效数字")

    sample_ids = [row["sample-id"] for row in normalised_rows if row["sample-id"]]
    duplicate_ids = sorted({value for value in sample_ids if sample_ids.count(value) > 1})
    if duplicate_ids:
        errors.append(f"样本 ID 重复：{', '.join(duplicate_ids)}")
    if not normalised_rows:
        errors.append("至少需要一行样本")
    if len(cleaned_headers) < 2:
        warnings.append("目前只有 sample-id；如果要做分组或差异分析，请添加一个 metadata 列")
    return {
        "valid": not errors,
        "sample_count": len(normalised_rows),
        "columns": cleaned_headers,
        "types": cleaned_types,
        "errors": errors,
        "warnings": warnings,
        "rows": normalised_rows,
    }


def save_metadata_table(path: str | Path, headers: Iterable[object], types: Iterable[object] | None, rows: Iterable[dict]) -> dict:
    result = validate_metadata_payload(headers, types, rows)
    if not result["valid"]:
        raise TableError("；".join(result["errors"]))
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(result["columns"])
        # The first value is the directive marker; the remaining values map to metadata columns.
        writer.writerow(["#q2:types", *result["types"][1:]])
        for row in result["rows"]:
            writer.writerow([row[header] for header in result["columns"]])
    result["path"] = str(output.resolve())
    return result


def _manifest_type(headers: Iterable[str]) -> str | None:
    normalised = {str(value).strip().lower() for value in headers}
    if "sample-id" not in normalised:
        return None
    if {"forward-absolute-filepath", "reverse-absolute-filepath"}.issubset(normalised):
        return "manifest_paired"
    if "absolute-filepath" in normalised or "forward-absolute-filepath" in normalised:
        return "manifest_single"
    return None


def _resolve_reference(value: str, manifest_path: Path, bundle_dir: Path | None) -> Path | None:
    reference = str(value or "").strip().removeprefix("file://")
    if not reference:
        return None
    raw = Path(reference)
    candidates = [raw] if raw.is_absolute() else [manifest_path.parent / raw, manifest_path.parent / raw.name]
    if bundle_dir:
        candidates.extend([bundle_dir / raw, bundle_dir / raw.name])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def preview_manifest(path: str | Path, bundle_dir: str | Path | None = None) -> dict:
    table = read_table(path)
    data_type = _manifest_type(table["headers"])
    if not data_type:
        raise TableError("manifest 必须包含 sample-id 和 filepath 列")
    expected = MANIFEST_HEADERS[data_type]
    header_map = {header.lower(): header for header in table["headers"]}
    columns = [header_map[header] for header in expected]
    rows = [{header: row.get(header, "") for header in columns} for row in table["rows"]]
    bundle = Path(bundle_dir) if bundle_dir else None
    path_status: list[dict[str, object]] = []
    references: list[str] = []
    missing: list[str] = []
    for row in rows:
        row_status: dict[str, object] = {"sample-id": row.get(columns[0], ""), "files": []}
        for column in columns[1:]:
            reference = row.get(column, "")
            resolved = _resolve_reference(reference, Path(path), bundle)
            references.append(reference)
            exists = resolved is not None
            if not exists and reference:
                missing.append(reference)
            row_status["files"].append({"column": column, "value": reference, "exists": exists, "resolved": str(resolved) if resolved else None})
        path_status.append(row_status)
    return {
        "path": str(Path(path).resolve()),
        "data_type": data_type,
        "headers": expected,
        "rows": rows,
        "sample_count": len(rows),
        "fastq_count": len([value for value in references if value]),
        "missing_files": missing,
        "path_status": path_status,
    }


def save_manifest_table(path: str | Path, data_type: str, rows: Iterable[dict]) -> dict:
    if data_type not in MANIFEST_HEADERS:
        raise TableError("manifest 类型必须是 manifest_single 或 manifest_paired")
    headers = list(MANIFEST_HEADERS[data_type])
    clean_rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_row in enumerate(rows, start=1):
        row = {header: _clean_cell(raw_row.get(header, "")) for header in headers}
        sample_id = row["sample-id"]
        if not sample_id:
            raise TableError(f"第 {index} 行的 sample-id 不能为空")
        if sample_id in seen:
            raise TableError(f"样本 ID 重复：{sample_id}")
        seen.add(sample_id)
        if any(not row[header] for header in headers[1:]):
            raise TableError(f"第 {index} 行的 FASTQ 路径不能为空")
        clean_rows.append(row)
    if not clean_rows:
        raise TableError("manifest 至少需要一行样本")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(headers)
        writer.writerows([row[header] for header in headers] for row in clean_rows)
    return preview_manifest(output, output.parent)


def metadata_preview(path: str | Path) -> dict:
    table = read_table(path)
    validation = validate_metadata_payload(table["headers"], table["types"], table["rows"])
    return {
        "path": str(Path(path).resolve()),
        "headers": table["headers"],
        "types": table["types"],
        "rows": table["rows"],
        "sample_count": len(table["rows"]),
        "validation": {key: value for key, value in validation.items() if key != "rows"},
    }
