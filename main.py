"""Command-line entry point for the business analysis prototype."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from business_analysis_agents.controller import run


if __name__ == "__main__":
    run()
