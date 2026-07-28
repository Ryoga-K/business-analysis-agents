"""Runtime configuration and output directory helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    """環境変数またはデフォルト値から決まる実行設定。"""

    openai_model: str = "gpt-5.6"
    max_repair_iterations: int = 3
    output_dir: Path = Path("outputs")

    @property
    def outputs_dir(self) -> Path:
        """旧名称との互換用に出力ディレクトリを返す。"""

        return self.output_dir


def load_config_from_env() -> AppConfig:
    """環境変数からアプリケーション設定を読み込む。"""

    max_repair_iterations = os.getenv("MAX_REPAIR_ITERATIONS") or "3"
    return AppConfig(
        openai_model=os.getenv("OPENAI_MODEL") or "gpt-5.6",
        max_repair_iterations=int(max_repair_iterations),
        output_dir=Path(os.getenv("OUTPUT_DIR") or "outputs"),
    )


def create_run_dir(base_dir: Path | str = "outputs") -> Path:
    """1回分の実行結果を保存するディレクトリを作成して返す。"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(base_dir) / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir
