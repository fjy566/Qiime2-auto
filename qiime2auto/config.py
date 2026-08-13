"""配置模型与参数校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CONFIG = {
    "trim_left_f": 0,
    "trim_left_r": 0,
    "trunc_len_f": 250,
    "trunc_len_r": 220,
    "classifier": "gg-13-8-99-515-806-nb-classifier.qza",
    "sampling_depth": "auto",
    "min_sample_retain": 0.95,
    "min_depth_percent": 0.85,
    "min_absolute_depth": 1000,
    # Metadata is optional. The pipeline can still import, denoise and
    # classify without it; group-dependent steps are skipped with a note.
    "metadata": "",
    "phred_offset": 33,
    "barcode_length": 12,
    "min_quality": 20,
    "min_frequency": 10,
}


class ConfigError(ValueError):
    """用户输入参数不符合约束。"""


@dataclass
class AnalysisConfig:
    trim_left_f: int = DEFAULT_CONFIG["trim_left_f"]
    trim_left_r: int = DEFAULT_CONFIG["trim_left_r"]
    trunc_len_f: int = DEFAULT_CONFIG["trunc_len_f"]
    trunc_len_r: int = DEFAULT_CONFIG["trunc_len_r"]
    classifier: str = DEFAULT_CONFIG["classifier"]
    sampling_depth: str | int = DEFAULT_CONFIG["sampling_depth"]
    min_sample_retain: float = DEFAULT_CONFIG["min_sample_retain"]
    min_depth_percent: float = DEFAULT_CONFIG["min_depth_percent"]
    min_absolute_depth: int = DEFAULT_CONFIG["min_absolute_depth"]
    metadata: str = DEFAULT_CONFIG["metadata"]
    phred_offset: int = DEFAULT_CONFIG["phred_offset"]
    barcode_length: int = DEFAULT_CONFIG["barcode_length"]
    min_quality: int = DEFAULT_CONFIG["min_quality"]
    min_frequency: int = DEFAULT_CONFIG["min_frequency"]
    max_ee: float | None = None
    trunc_q: int | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "AnalysisConfig":
        known = {field.name for field in cls.__dataclass_fields__.values()}
        data = {key: value for key, value in values.items() if key in known}
        config = cls(**data)
        config.validate()
        return config

    def validate(self) -> None:
        non_negative = {
            "trim_left_f": self.trim_left_f,
            "trim_left_r": self.trim_left_r,
            "trunc_len_f": self.trunc_len_f,
            "trunc_len_r": self.trunc_len_r,
            "min_absolute_depth": self.min_absolute_depth,
            "barcode_length": self.barcode_length,
            "min_quality": self.min_quality,
            "min_frequency": self.min_frequency,
        }
        for name, value in non_negative.items():
            if value < 0:
                raise ConfigError(f"{name} 不能小于 0")
        if self.phred_offset not in (33, 64):
            raise ConfigError("phred-offset 只能是 33 或 64")
        if not 0 < self.min_sample_retain <= 1:
            raise ConfigError("min-sample-retain 必须在 (0, 1] 范围内")
        if not 0 < self.min_depth_percent <= 1:
            raise ConfigError("min-depth-percent 必须在 (0, 1] 范围内")
        if self.sampling_depth != "auto":
            try:
                if int(self.sampling_depth) <= 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ConfigError("sampling-depth 必须是正整数或 auto") from exc
        if self.max_ee is not None and self.max_ee <= 0:
            raise ConfigError("max-ee 必须大于 0")
        if self.trunc_q is not None and self.trunc_q < 0:
            raise ConfigError("trunc-q 不能小于 0")

    def as_dict(self) -> dict[str, Any]:
        return {
            key: str(value) if isinstance(value, Path) else value
            for key, value in self.__dict__.items()
        }
