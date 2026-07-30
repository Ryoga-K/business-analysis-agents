"""Workflow Agent with ontology setup, RDF generation, and revision modes."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from agents import Agent, AgentOutputSchema, Runner

from business_analysis_agents.models import WorkflowAgentMode, WorkflowAgentOutput


WORKFLOW_AGENT_NAME = "workflow_agent"

BASE_WORKFLOW_INSTRUCTIONS = """
You are the only AI agent responsible for Workflow RDF extraction.
Do not create or delegate to an Ontology Design Agent.

General rules:
- Treat ontology design as an internal mode of this Workflow Agent.
- Use the supplied business scenario JSON as the only business source.
- Do not infer activities, actors, data, conditions, order, or evidence that are not in the scenario.
- Do not complete uncertain information aggressively. Put it in unresolved items.
- Use one WorkflowAgentOutput Pydantic model for every mode.
- For Workflow RDF, output complete Turtle text in workflow_rdf_turtle.
- Do not wrap Turtle in Markdown fences.
- During workflow_revision, do not change ontology_turtle or shacl_turtle.
""".strip()

ONTOLOGY_SETUP_INSTRUCTIONS = """
Mode: ontology_setup
Analyze the scenario JSON and decide the provisional vocabulary required for Workflow RDF.
Refer to basic BPMN concepts and reuse RDF, RDFS, XSD, and PROV-O where practical.
Use an RDFS-level ontology, not complex OWL reasoning.

Represent at least:
- workflow as a whole
- activity
- actor
- input data
- output data
- activity order
- branch condition
- extraction evidence
- unresolved item

You may use class names such as Workflow, Activity, Actor, DataObject, Condition, Evidence.
You may use property names such as hasActivity, performedBy, hasInput, hasOutput,
precedes, hasCondition, hasEvidence, sourcePage, evidenceText.
You do not have to adopt these exact names; choose terms that fit the scenario and document
the mapping in class_property_mapping.

Generate:
- ontology_turtle
- shacl_turtle
- reused_standard_terms
- provisional_classes
- provisional_properties
- class_property_mapping
- ontology_design_notes
- shacl_design_notes
- unresolved_design_issues
- ontology_version
- namespace_uri
""".strip()

WORKFLOW_GENERATION_INSTRUCTIONS = """
Mode: workflow_generation
Use only the fixed ontology_turtle and shacl_turtle supplied in the prompt.
Generate Workflow RDF directly as complete Turtle text.

Rules:
- Use only classes and properties available in the fixed ontology and common RDF/RDFS/XSD/PROV terms.
- Do not add new classes, properties, or namespaces.
- Use the namespace URI naming policy supplied in the prompt.
- Include evidence page number and evidence text for main activities and conditions.
- Create order relations only when order is explicit in the scenario.
- Avoid assigning multiple URIs to the same conceptual entity.
- Put unresolved items in unresolved_items instead of inventing missing information.
""".strip()

WORKFLOW_REVISION_INSTRUCTIONS = """
Mode: workflow_revision
Revise only workflow_rdf_turtle.

Forbidden:
- Changing ontology_turtle
- Changing shacl_turtle
- Adding new vocabulary terms
- Changing namespaces
- Weakening constraints
- Adding unsupported business information

Use the RDFLib parse errors, vocabulary validation result, SHACL validation result,
and revision history to fix the Workflow RDF. If a problem cannot be fixed without
unsupported business assumptions, keep it in remaining_violations and unresolved_items.
""".strip()


class MissingOpenAIAPIKeyError(RuntimeError):
    """Raised when OPENAI_API_KEY is missing."""


def ensure_openai_api_key() -> None:
    """Ensure the OpenAI API key is available from the environment."""

    if not os.getenv("OPENAI_API_KEY"):
        raise MissingOpenAIAPIKeyError(
            "OPENAI_API_KEY is not set. Set it in the environment or .env before running Workflow Agent."
        )


def build_workflow_agent(model: str) -> Agent:
    """Build the single Agents SDK Workflow Agent."""

    return Agent(
        name=WORKFLOW_AGENT_NAME,
        instructions=BASE_WORKFLOW_INSTRUCTIONS,
        model=model,
        output_type=AgentOutputSchema(
            WorkflowAgentOutput,
            strict_json_schema=False,
        ),
    )


def _mode_instructions(mode: WorkflowAgentMode) -> str:
    if mode is WorkflowAgentMode.ONTOLOGY_SETUP:
        return ONTOLOGY_SETUP_INSTRUCTIONS
    if mode is WorkflowAgentMode.WORKFLOW_GENERATION:
        return WORKFLOW_GENERATION_INSTRUCTIONS
    return WORKFLOW_REVISION_INSTRUCTIONS


def build_workflow_prompt(mode: WorkflowAgentMode, payload: dict[str, Any]) -> str:
    """Build a mode-specific Workflow Agent prompt."""

    return (
        f"{_mode_instructions(mode)}\n\n"
        f"Return WorkflowAgentOutput with mode='{mode.value}'.\n\n"
        f"Input payload:\n{payload}"
    )


def run_workflow_agent(
    mode: WorkflowAgentMode,
    payload: dict[str, Any],
    model: str,
    runner: Callable[..., Any] | None = None,
) -> WorkflowAgentOutput:
    """Run the single Workflow Agent in the requested internal mode."""

    ensure_openai_api_key()
    agent = build_workflow_agent(model)
    prompt = build_workflow_prompt(mode, payload)
    run = runner or Runner.run_sync
    result = run(agent, prompt)
    final_output = getattr(result, "final_output", result)
    if isinstance(final_output, WorkflowAgentOutput):
        return final_output
    return WorkflowAgentOutput.model_validate(final_output)
