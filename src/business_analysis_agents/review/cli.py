"""Command-line human review helpers."""

from __future__ import annotations

from business_analysis_agents.models import ShaclViolation


def request_human_decision(violation: ShaclViolation) -> str:
    """Ask a human reviewer how to handle an unresolved violation."""

    print(f"SHACL違反: {violation.message}")
    return input("対応方針を入力してください: ")
