from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

_PREFIX = "GAMECRAFT_BENCH_EVALUATION_"
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[4] / ".cache" / "evaluations"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = _DEFAULT_DATA_DIR
    api_token: str | None = None
    max_videos: int = 10
    max_video_bytes: int = 100 * 1024 * 1024
    max_total_bytes: int = 500 * 1024 * 1024
    max_rubric_bytes: int = 1024 * 1024
    max_requirements: int = 50
    max_demo_seconds: float = 60.0
    max_formula_length: int = 4096
    max_formula_nodes: int = 256
    max_description_length: int = 20_000
    command_timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> Settings:
        values: dict[str, object] = {}
        for field in cls.__dataclass_fields__.values():
            raw = os.getenv(_PREFIX + field.name.upper())
            if raw is None:
                continue
            if field.name == "data_dir":
                values[field.name] = Path(raw).expanduser()
            elif field.name == "api_token":
                values[field.name] = raw or None
            elif field.type == "float" or field.name in {
                "max_demo_seconds", "command_timeout_seconds"
            }:
                values[field.name] = float(raw)
            else:
                values[field.name] = int(raw)
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self) -> None:
        for name in (
            "max_videos", "max_video_bytes", "max_total_bytes",
            "max_rubric_bytes", "max_requirements", "max_formula_length",
            "max_formula_nodes", "max_description_length",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if any(not math.isfinite(value) or value <= 0 for value in (
            self.max_demo_seconds, self.command_timeout_seconds,
        )):
            raise ValueError("time limits must be finite and positive")
        if self.max_videos > 10:
            raise ValueError("max_videos cannot exceed 10")
