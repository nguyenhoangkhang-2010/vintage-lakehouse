"""Runtime configuration resolved from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_DATA_DIR = "VINTAGE_LAKEHOUSE_DATA_DIR"
DEFAULT_DATA_DIR = "data"


@dataclass(frozen=True)
class Settings:
    """Immutable settings for one run of the tool."""

    data_dir: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``environ`` (defaults to ``os.environ``)."""
        env = os.environ if environ is None else environ
        return cls(data_dir=Path(env.get(ENV_DATA_DIR, DEFAULT_DATA_DIR)).expanduser())
