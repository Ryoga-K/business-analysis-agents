"""Package entry point for python -m business_analysis_agents."""

from __future__ import annotations

import sys

from business_analysis_agents.controller import run


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
