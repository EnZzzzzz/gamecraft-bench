"""Types shared by Godot and browser replay backends."""

from __future__ import annotations

import dataclasses
from pathlib import Path


class ReplayError(RuntimeError):
    """Raised when a replay pipeline fails to start, run, or finish."""


@dataclasses.dataclass(frozen=True)
class ReplayResult:
    output_mp4: Path
    duration_seconds: float
    runtime_returncode: int | None

    @property
    def godot_returncode(self) -> int | None:
        """Backward-compatible name for callers of the original Godot API."""
        return self.runtime_returncode


__all__ = ["ReplayError", "ReplayResult"]
