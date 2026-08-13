"""统一的外部命令执行器。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


class CommandError(RuntimeError):
    def __init__(self, command: list[str], message: str, returncode: int | None = None):
        self.command = command
        self.returncode = returncode
        super().__init__(message)


def command_text(command: Iterable[str]) -> str:
    values = [str(value) for value in command]
    return subprocess.list2cmdline(values) if os.name == "nt" else " ".join(_quote(value) for value in values)


def _quote(value: str) -> str:
    return value if value and all(char.isalnum() or char in "-._/:=" for char in value) else repr(value)


class CommandRunner:
    def __init__(self, output_dir: str | Path | None = None, dry_run: bool = False, command_prefix: Iterable[str] | None = None):
        self.output_dir = Path(output_dir) if output_dir else None
        self.dry_run = dry_run
        self.command_prefix = [str(value) for value in (command_prefix or [])]
        if self.output_dir:
            (self.output_dir / "logs").mkdir(parents=True, exist_ok=True)

    def available(self, executable: str) -> bool:
        return shutil.which(executable) is not None

    def missing(self, executables: Iterable[str]) -> list[str]:
        return [name for name in executables if not self.available(name)]

    def require(self, executables: Iterable[str]) -> None:
        missing = self.missing(executables)
        if missing and not self.dry_run:
            raise CommandError(list(missing), f"缺少外部命令: {', '.join(missing)}。请在 QIIME2 环境中运行，或先使用 scan/serve 检查输入。")

    def run(self, command: Iterable[str], log_name: str | None = None) -> subprocess.CompletedProcess:
        values = [str(value) for value in command]
        if self.command_prefix and values and values[0] == "qiime":
            values = [*self.command_prefix, *values]
        print(f"\n$ {command_text(values)}")
        if self.dry_run:
            return subprocess.CompletedProcess(values, 0, "[dry-run]\n", "")
        log_handle = None
        try:
            if log_name and self.output_dir:
                log_handle = (self.output_dir / "logs" / log_name).open("a", encoding="utf-8")
                log_handle.write(f"\n$ {command_text(values)}\n")
            completed = subprocess.run(values, check=True, text=True, stdout=log_handle, stderr=log_handle)
            return completed
        except FileNotFoundError as exc:
            raise CommandError(values, f"找不到命令: {values[0]}") from exc
        except subprocess.CalledProcessError as exc:
            raise CommandError(values, f"命令执行失败，退出码 {exc.returncode}。请查看 logs/ 目录。", exc.returncode) from exc
        finally:
            if log_handle:
                log_handle.close()
