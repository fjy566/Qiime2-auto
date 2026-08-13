"""Conda 环境发现、QIIME2 探测和安全安装命令生成。"""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_INSTALL_VERSIONS = ("2024.10", "2025.4", "2025.7")
SUPPORTED_DISTRIBUTIONS = ("amplicon", "tiny")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass
class CondaEnvironment:
    name: str
    prefix: str
    active: bool = False
    qiime_available: bool = False
    qiime_version: str | None = None
    probe_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _run(command: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)


def _parse_conda_env_list(output: str) -> list[CondaEnvironment]:
    environments: list[CondaEnvironment] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        active = " * " in f" {raw_line} " or raw_line.rstrip().endswith("*")
        line = line.replace("*", " ")
        parts = line.split()
        if len(parts) < 2:
            continue
        prefix = parts[-1]
        name = parts[0]
        if Path(prefix).is_absolute() or prefix.startswith(("/", "~", os.sep)) or re.match(r"^[A-Za-z]:[\\/].*", prefix):
            environments.append(CondaEnvironment(name=name, prefix=str(Path(prefix).expanduser()), active=active))
    return environments


def _probe_environment(conda: str, environment: CondaEnvironment) -> CondaEnvironment:
    selector = ["-p", environment.prefix] if environment.prefix else ["-n", environment.name]
    try:
        result = _run([conda, "run", "--no-capture-output", *selector, "qiime", "--version"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        environment.probe_error = str(exc)
        return environment
    combined = "\n".join(value for value in (result.stdout, result.stderr) if value).strip()
    match = re.search(r"(?:QIIME\s*2|qiime2?)[^\d]*(\d+\.\d+(?:\.\d+)?)", combined, re.IGNORECASE)
    if result.returncode == 0:
        environment.qiime_available = True
        environment.qiime_version = match.group(1) if match else combined.splitlines()[0] if combined else "可用"
    else:
        environment.probe_error = combined[-300:] or f"退出码 {result.returncode}"
    return environment


def discover_environments(probe: bool = True) -> dict:
    conda = shutil.which("conda")
    if not conda:
        return {"conda_available": False, "conda_path": None, "active": None, "environments": [], "error": "未找到 conda。请在 Linux 终端加载 Conda 后重新刷新。"}
    try:
        result = _run([conda, "env", "list"], timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"conda_available": True, "conda_path": conda, "active": None, "environments": [], "error": f"执行 conda env list 失败: {exc}"}
    if result.returncode != 0:
        return {"conda_available": True, "conda_path": conda, "active": None, "environments": [], "error": (result.stderr or result.stdout).strip()[-500:]}
    environments = _parse_conda_env_list(result.stdout)
    if not environments:
        try:
            json_result = _run([conda, "env", "list", "--json"], timeout=15)
            payload = json.loads(json_result.stdout) if json_result.returncode == 0 else {}
            environments = [CondaEnvironment(name="base" if index == 0 else Path(prefix).name, prefix=str(Path(prefix).expanduser())) for index, prefix in enumerate(payload.get("envs", []))]
        except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
            environments = []
    if probe:
        environments = [_probe_environment(conda, environment) for environment in environments]
    active = next((environment.name for environment in environments if environment.active), None)
    return {"conda_available": True, "conda_path": conda, "active": active, "environments": [environment.to_dict() for environment in environments], "error": None}


def install_command(version: str, distribution: str = "amplicon", environment_name: str | None = None) -> list[str]:
    if version not in SUPPORTED_INSTALL_VERSIONS:
        raise ValueError(f"不支持的 QIIME2 版本: {version}")
    if distribution not in SUPPORTED_DISTRIBUTIONS:
        raise ValueError(f"不支持的 QIIME2 分发版: {distribution}")
    name = environment_name or f"qiime2-{distribution}-{version}"
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError("Conda 环境名只能包含字母、数字、点、下划线和短横线")
    # 2024.10 的 URL 来自 QIIME2 官方安装文档；后续发行版沿用官方 distro
    # 文件命名规则，用户仍可在执行前复制并检查命令。
    url = f"https://data.qiime2.org/distro/{distribution}/qiime2-{distribution}-{version}-py310-linux-conda.yml"
    return ["conda", "env", "create", "-n", name, "--file", url]


def install_options() -> dict:
    return {"versions": list(SUPPORTED_INSTALL_VERSIONS), "distributions": list(SUPPORTED_DISTRIBUTIONS), "platform": "linux", "docs_url": "https://amplicon-docs.qiime2.org/en/stable/how-to-guides/install/"}
