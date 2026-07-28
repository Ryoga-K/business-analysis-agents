"""Runtime configuration and output directory helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    """Configuration values controlled by environment variables or defaults."""

    openai_model: str = "gpt-5.6"
    max_repair_iterations: int = 3
    outputs_dir: Path = Path("outputs")


def create_run_dir(base_dir: Path | str = "outputs") -> Path:
    """Create and return an output directory for a single execution."""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(base_dir) / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir
