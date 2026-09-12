"""Repair loop orchestration."""

from __future__ import annotations

from business_analysis_agents.config import MAX_REVISION_ITERATIONS
from business_analysis_agents.models import ShaclValidationResult


def should_continue_repair(
    validation_result: ShaclValidationResult,
    iteration: int,
) -> bool:
    """Return whether the Python-controlled repair loop should continue."""

    if validation_result.conforms:
        return False
    return iteration < MAX_REVISION_ITERATIONS
