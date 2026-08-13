"""分析前置检查与可执行步骤规划。

这个模块不调用 QIIME2，只读取用户已经选择的输入，回答两个问题：
1. 当前配置能不能开始；
2. 如果不能或不需要，哪些步骤会被阻止、跳过或降级。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from .table_editor import TableError, read_table, validate_metadata_payload


SUPPORTED_DATA_TYPES = {
    "EMP_single",
    "EMP_paired",
    "Casava_single",
    "Casava_paired",
    "manifest_single",
    "manifest_paired",
    "muxed_single",
    "muxed_paired",
}


def inspect_metadata_capabilities(metadata_path: str | Path | None) -> dict[str, Any]:
    """读取 metadata 的能力边界，特别是是否存在可用于比较的分类分组列。"""

    base: dict[str, Any] = {
        "provided": bool(str(metadata_path or "").strip()),
        "path": str(Path(metadata_path).expanduser().resolve()) if metadata_path else None,
        "valid": False,
        "usable": False,
        "sample_count": 0,
        "columns": [],
        "types": [],
        "categorical_columns": [],
        "group_column": None,
        "group_values": [],
        "group_ready": False,
        "group_message": "未提供 metadata。",
        "errors": [],
        "warnings": [],
    }
    if not metadata_path:
        return base

    path = Path(metadata_path).expanduser()
    if not path.is_file():
        base["errors"] = ["metadata 文件不存在"]
        base["group_message"] = "metadata 文件不存在，无法进行分组分析。"
        return base

    try:
        table = read_table(path)
        validation = validate_metadata_payload(table["headers"], table["types"], table["rows"])
    except (OSError, UnicodeError, ValueError, TableError) as exc:
        base["errors"] = [f"无法读取 metadata：{exc}"]
        base["group_message"] = "metadata 无法读取，无法进行分组分析。"
        return base

    headers = list(table["headers"])
    types = list(table["types"])
    rows = list(table["rows"])
    base.update(
        {
            "valid": bool(validation.get("valid")),
            "usable": bool(validation.get("valid")),
            "sample_count": len(rows),
            "columns": headers,
            "types": types,
            "errors": list(validation.get("errors", [])),
            "warnings": list(validation.get("warnings", [])),
        }
    )

    candidates: list[dict[str, Any]] = []
    for index, column in enumerate(headers[1:], start=1):
        column_type = types[index] if index < len(types) else ""
        if column_type == "numeric":
            continue
        values = sorted({str(row.get(column, "")).strip() for row in rows if str(row.get(column, "")).strip()})
        complete = bool(rows) and all(str(row.get(column, "")).strip() for row in rows)
        candidates.append(
            {
                "column": column,
                "type": column_type or "categorical",
                "values": values,
                "complete": complete,
                "distinct_count": len(values),
                "ready": complete and len(values) >= 2,
            }
        )

    base["categorical_columns"] = [item["column"] for item in candidates]
    preferred = next((item for item in candidates if item["column"].lower() == "group" and item["ready"]), None)
    preferred = preferred or next((item for item in candidates if item["ready"]), None)
    if preferred:
        base["group_column"] = preferred["column"]
        base["group_values"] = preferred["values"]
        base["group_ready"] = True
        base["group_message"] = f"可用分组列：{preferred['column']}（{len(preferred['values'])} 组）"
    elif candidates:
        named_group = next((item for item in candidates if item["column"].lower() == "group"), candidates[0])
        base["group_column"] = named_group["column"]
        base["group_values"] = named_group["values"]
        base["group_message"] = (
            f"列 {named_group['column']} 需要至少两个完整分组值；当前只有 "
            f"{len(named_group['values'])} 个不同值或存在空值。"
        )
    else:
        base["group_message"] = "没有 categorical 分组列；ANCOM 需要一个完整的分类列。"

    return base


def _step(step_id: str, label: str, status: str, message: str) -> dict[str, str]:
    return {"id": step_id, "label": label, "status": status, "message": message}


def build_preflight(data: Mapping[str, Any]) -> dict[str, Any]:
    """根据用户选择生成可解释的分析计划，不启动任何外部命令。"""

    platform = str(data.get("platform") or ("posix" if os.name == "posix" else "nt"))
    input_path = str(data.get("input_path") or "").strip()
    output_dir = str(data.get("output_dir") or "").strip()
    data_type = str(data.get("data_type") or "").strip()
    metadata_path = str(data.get("metadata") or "").strip()
    qiime_env = str(data.get("qiime_env") or "").strip()
    classifier_path = str(data.get("classifier") or "").strip()
    skip_taxonomy = bool(data.get("skip_taxonomy"))
    skip_diversity = bool(data.get("skip_diversity"))
    skip_ancom = bool(data.get("skip_ancom"))
    no_trim = bool(data.get("no_trim"))
    no_filter = bool(data.get("no_filter"))
    no_figaro = bool(data.get("no_figaro"))
    sampling_depth = str(data.get("sampling_depth") or "auto").strip().lower()

    metadata = inspect_metadata_capabilities(metadata_path)
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    steps: list[dict[str, str]] = []

    if not input_path:
        blockers.append({"id": "input", "title": "输入数据", "message": "请选择 manifest 或 FASTQ 数据。"})
    elif not Path(input_path).expanduser().exists():
        blockers.append({"id": "input", "title": "输入数据", "message": "输入文件或目录不存在，请重新选择服务器路径。"})
    if not data_type:
        blockers.append({"id": "data_type", "title": "输入格式", "message": "还没有识别出数据类型，请先扫描输入。"})
    elif data_type not in SUPPORTED_DATA_TYPES:
        blockers.append({"id": "data_type", "title": "输入格式", "message": f"暂不支持数据类型：{data_type}。请重新选择 manifest 或 FASTQ。"})
    if platform != "posix":
        blockers.append({"id": "platform", "title": "运行平台", "message": "完整分析需要在 Linux + Conda + QIIME2 服务器执行。"})
    if not qiime_env:
        blockers.append({"id": "qiime_env", "title": "QIIME2 环境", "message": "请选择一个包含 QIIME2 的 Conda 环境。"})
    if not output_dir:
        blockers.append({"id": "output", "title": "输出目录", "message": "请选择结果保存目录。"})
    elif Path(output_dir).expanduser().exists() and not Path(output_dir).expanduser().is_dir():
        blockers.append({"id": "output", "title": "输出目录", "message": "输出路径已经存在，但它不是文件夹；请选择一个文件夹。"})
    if data.get("qiime_env_available") is False:
        blockers.append({"id": "qiime_env", "title": "QIIME2 环境", "message": "所选 Conda 环境当前没有可用的 QIIME2，请重新选择环境或刷新环境列表。"})

    if skip_taxonomy:
        steps.append(_step("taxonomy", "物种分类", "skipped", "你选择了跳过物种分类，分类器不会参与本次分析。"))
    elif not classifier_path:
        blockers.append({"id": "classifier", "title": "分类器", "message": "物种分类已开启，但还没有分类器；请选择 .qza 或勾选跳过物种分类。"})
        steps.append(_step("taxonomy", "物种分类", "blocked", "缺少分类器，无法开始。"))
    elif not Path(classifier_path).expanduser().is_file():
        blockers.append({"id": "classifier", "title": "分类器", "message": "分类器路径不存在，请重新选择 .qza 或下载官方分类器。"})
        steps.append(_step("taxonomy", "物种分类", "blocked", "分类器文件不存在。"))
    else:
        steps.append(_step("taxonomy", "物种分类", "run", "分类器已准备好。"))

    diversity_can_run = bool(metadata["usable"])
    biom_auto_unavailable = (
        not skip_diversity
        and diversity_can_run
        and sampling_depth == "auto"
        and data.get("biom_available") is False
    )
    effective_skip_diversity = skip_diversity or not diversity_can_run or biom_auto_unavailable
    if skip_diversity:
        steps.append(_step("diversity", "多样性分析", "skipped", "你选择了跳过多样性，采样深度不会参与本次分析。"))
    elif not metadata["provided"]:
        steps.append(_step("diversity", "多样性分析", "skipped", "没有 metadata，本次自动跳过需要样本信息的多样性分析。"))
        warnings.append({"id": "metadata", "title": "多样性已跳过", "message": "没有 metadata；DADA2 和基础物种分类仍可做，但 core metrics 不会执行。"})
    elif not metadata["usable"]:
        steps.append(_step("diversity", "多样性分析", "skipped", "metadata 校验未通过，本次自动跳过多样性分析。"))
        warnings.append({"id": "metadata", "title": "多样性已跳过", "message": "metadata 未通过校验；请修正后才能运行 core metrics。"})
    elif biom_auto_unavailable:
        steps.append(_step("diversity", "多样性分析", "skipped", "当前环境没有 biom，自动采样深度无法计算，本次跳过多样性分析。"))
        warnings.append({"id": "biom", "title": "自动采样深度不可用", "message": "当前 Conda 环境没有 biom；本次跳过自动采样深度和多样性分析。也可以改成正整数后重新运行。"})
    else:
        steps.append(_step("diversity", "多样性分析", "run", "metadata 已准备好，采样深度会用于 core metrics。"))

    effective_skip_ancom = skip_ancom or skip_taxonomy or not metadata["usable"] or not metadata["group_ready"]
    if skip_ancom:
        steps.append(_step("ancom", "ANCOM 差异分析", "skipped", "你选择了跳过差异分析。"))
    elif skip_taxonomy:
        steps.append(_step("ancom", "ANCOM 差异分析", "skipped", "跳过物种分类后，没有 taxonomy 可用于 ANCOM。"))
        warnings.append({"id": "ancom", "title": "ANCOM 已跳过", "message": "跳过物种分类后，没有 taxonomy 可用于 ANCOM。"})
    elif not metadata["provided"]:
        steps.append(_step("ancom", "ANCOM 差异分析", "skipped", "没有 metadata，本次自动跳过差异分析。"))
        warnings.append({"id": "ancom", "title": "ANCOM 已跳过", "message": "没有 metadata 分组信息，无法按组比较丰度差异。"})
    elif not metadata["usable"]:
        steps.append(_step("ancom", "ANCOM 差异分析", "skipped", "metadata 无法使用，本次自动跳过差异分析。"))
        warnings.append({"id": "ancom", "title": "ANCOM 已跳过", "message": "metadata 未通过校验，无法按组比较丰度差异。"})
    elif not metadata["group_ready"]:
        steps.append(_step("ancom", "ANCOM 差异分析", "skipped", metadata["group_message"]))
        warnings.append({"id": "ancom", "title": "ANCOM 已跳过", "message": f"{metadata['group_message']} ANCOM 需要至少两个完整分组。"})
    else:
        steps.append(_step("ancom", "ANCOM 差异分析", "run", f"使用 {metadata['group_column']} 列比较 {len(metadata['group_values'])} 个分组。"))

    steps.insert(0, _step("input", "导入数据", "run", "输入数据已准备好。"))
    if no_trim:
        steps.append(_step("trim", "引物去除", "skipped", "你选择了跳过引物去除。"))
    else:
        steps.append(_step("trim", "引物去除", "run", "按当前引物设置执行；未填写引物时不会额外去除。"))
    steps.append(_step("filter", "质量过滤", "skipped" if no_filter else "run", "你选择了跳过质量过滤。" if no_filter else "按最低质量设置过滤。"))
    figaro_available = data.get("figaro_available")
    if no_figaro:
        steps.append(_step("figaro", "Figaro", "skipped", "你选择了跳过 Figaro，将使用手动截断长度。"))
    elif figaro_available is False:
        steps.append(_step("figaro", "Figaro", "skipped", "当前环境没有 Figaro，将使用手动截断长度。"))
        warnings.append({"id": "figaro", "title": "Figaro 未安装", "message": "本次不会使用 Figaro；如需自动截断建议，请先安装 Figaro。"})
    else:
        steps.append(_step("figaro", "Figaro", "run", "如果当前环境可用，将生成自动截断建议。"))
    steps.append(_step("dada2", "DADA2 去噪", "run", "生成 feature table 和代表序列。"))
    steps.append(
        _step(
            "sampling",
            "采样深度",
            "skipped" if effective_skip_diversity else "run",
            "跳过多样性后不需要计算采样深度。" if effective_skip_diversity else "自动推荐或使用你填写的固定值。",
        )
    )

    if not effective_skip_diversity and sampling_depth != "auto":
        try:
            if int(sampling_depth) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            blockers.append({"id": "sampling_depth", "title": "采样深度", "message": "自定义采样深度必须是正整数，或选择自动推荐。"})

    return {
        "can_run": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "steps": steps,
        "metadata": metadata,
        "effective": {
            "skip_ancom": effective_skip_ancom,
            "skip_diversity": effective_skip_diversity,
            "skip_taxonomy": skip_taxonomy,
        },
    }
