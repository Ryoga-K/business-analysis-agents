"""Consistency evaluation agent skeleton."""

from __future__ import annotations

from business_analysis_agents.models import (
    ConsistencyEvaluationInput,
    ConsistencyEvaluationOutput,
)


def run_consistency_agent(
    agent_input: ConsistencyEvaluationInput,
) -> ConsistencyEvaluationOutput:
    """Evaluate SHACL violations and choose a repair target.

    TODO: Use an LLM only to interpret violations and suggest the extraction
    agent to rerun. Do not use the LLM for SHACL conformance decisions.
    """

    _ = agent_input
    raise NotImplementedError("Consistency evaluation agent is not implemented yet.")
