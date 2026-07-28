"""Repair loop orchestration."""

from __future__ import annotations

from business_analysis_agents.config import AppConfig
from business_analysis_agents.models import ShaclValidationResult


def should_continue_repair(
    validation_result: ShaclValidationResult,
    iteration: int,
    config: AppConfig,
) -> bool:
    """Return whether the Python-controlled repair loop should continue."""

    if validation_result.conforms:
        return False
    return iteration < config.max_repair_iterations
