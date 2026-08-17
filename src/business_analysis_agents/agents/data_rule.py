"""Related data and rule extraction agent."""

from __future__ import annotations

import os
import json
from collections.abc import Callable
from typing import Any

from agents import Agent, AgentOutputSchema, Runner
from agents.exceptions import ModelBehaviorError

from business_analysis_agents.models import DataRuleAgentMode, DataRuleAgentOutput


DATA_RULE_AGENT_NAME = "related_data_rule_agent"

BASE_DATA_RULE_INSTRUCTIONS = """
You are the only AI agent responsible for related data and rule extraction.
Do not create separate Data Agent, Rule Agent, or Ontology Design Agent.

General rules:
- Use the supplied business scenario JSON as the only business source.
- Do not reread or infer from the original PDF.
- Do not infer data, attributes, relationships, judgment conditions, or outcomes that are not in the scenario.
- Use evidence page numbers and evidence text already present in the scenario.
- Keep uncertain or unsupported content in unresolved_items.
- Output Data RDF and Rule RDF directly as complete Turtle text when requested.
- Do not build intermediate business RDF JSON.
- RDF syntax, vocabulary, and SHACL validity are checked by RDFLib and pySHACL, not by Pydantic.
- Explanatory and design-oriented fields may use flexible JSON structures.
- During revision modes, do not change ontology_turtle or SHACL shapes.
- Do not wrap Turtle in Markdown fences.
""".strip()

ONTOLOGY_SETUP_INSTRUCTIONS = """
Mode: ontology_setup
Analyze the scenario JSON and design provisional RDFS terms for Data RDF and Rule RDF.
Reuse RDF, RDFS, XSD, and PROV-O only where the terms really exist and fit.
Do not invent URIs for external standard vocabularies.

Consider provisional Data RDF concepts such as:
DataEntity, Document, Form, ApplicationForm, Notification, Result, DataAttribute,
Evidence, UnresolvedItem.

Consider provisional Data RDF properties such as:
hasAttribute, relatedTo, usedBy, generatedBy, submittedBy, issuedBy, managedBy,
hasEvidence, hasUnresolvedItem.

Consider provisional Rule RDF concepts such as:
BusinessRule, DecisionRule, EligibilityRule, Condition, Criterion, RuleOutcome,
Evidence, UnresolvedItem.

Consider provisional Rule RDF properties such as:
hasCondition, usesData, producesResult, appliesTo, evaluatedBy, hasOutcome,
hasEvidence, hasUnresolvedItem, dependsOnRule.

Generate:
- ontology_turtle (required)
- data_shacl_turtle (required)
- rule_shacl_turtle (required)
- reused_standard_terms
- provisional_classes
- provisional_properties
- class_property_mapping
- ontology_design_notes
- data_shacl_design_notes
- rule_shacl_design_notes
- unresolved_design_issues
- ontology_version
- namespace_uri
""".strip()

DATA_GENERATION_INSTRUCTIONS = """
Mode: data_generation
Use only the fixed ontology_turtle and fixed Data SHACL shapes supplied in the prompt.
Generate Data RDF directly as complete Turtle text.

Represent only data concepts confirmed in the scenario, including documents, forms,
notifications, results, data attributes explicitly described, data usage/generation
by business activities, responsible actors when explicitly stated, evidence, and
unresolved items.

Do not add generic form attributes that are not written in the scenario.
Use normalized identifiers to avoid duplicate URIs for the same data concept.
data_rdf_turtle is required.
""".strip()

DATA_REVISION_INSTRUCTIONS = """
Mode: data_revision
Revise only data_rdf_turtle.

Use RDFLib parse errors, vocabulary validation, pySHACL results, revision history,
and the original scenario JSON. Do not modify ontology_turtle or Data SHACL shapes.
Do not add unsupported scenario information. Remove unsupported information or keep
it as unresolved when appropriate.
data_rdf_turtle is required.
""".strip()

RULE_GENERATION_INSTRUCTIONS = """
Mode: rule_generation
Use only the fixed ontology_turtle, fixed Rule SHACL shapes, scenario JSON, and
validated Data RDF supplied in the prompt.
Generate Rule RDF directly as complete Turtle text.

Represent only judgment conditions, eligibility/application conditions, exclusion
conditions, required documents, outcomes, and data references confirmed in the
scenario. Do not invent numeric formulas, thresholds, AND/OR/NOT structures, or
formal expressions unless explicit in the scenario.
When referencing data, reuse entities defined in the validated Data RDF where possible.
rule_rdf_turtle is required.
""".strip()

RULE_REVISION_INSTRUCTIONS = """
Mode: rule_revision
Revise only rule_rdf_turtle.

Use the previous Rule RDF, validated Data RDF, fixed ontology_turtle, fixed Rule
SHACL shapes, RDFLib parse errors, vocabulary validation, pySHACL results, revision
history, and the original scenario JSON. Do not modify ontology_turtle or Rule
SHACL shapes. Do not add unsupported business rules.
rule_rdf_turtle is required.
""".strip()


class MissingOpenAIAPIKeyError(RuntimeError):
    """Raised when OPENAI_API_KEY is missing."""


def ensure_openai_api_key() -> None:
    """Ensure the OpenAI API key is available from the environment."""

    if not os.getenv("OPENAI_API_KEY"):
        raise MissingOpenAIAPIKeyError(
            "OPENAI_API_KEY is not set. Set it in the environment or .env before running Data/Rule Agent."
        )


def build_data_rule_agent(model: str) -> Agent:
    """Build the single Agents SDK related data/rule extraction agent."""

    return Agent(
        name=DATA_RULE_AGENT_NAME,
        instructions=BASE_DATA_RULE_INSTRUCTIONS,
        model=model,
        output_type=AgentOutputSchema(
            DataRuleAgentOutput,
            strict_json_schema=False,
        ),
    )


def _mode_instructions(mode: DataRuleAgentMode) -> str:
    if mode is DataRuleAgentMode.ONTOLOGY_SETUP:
        return ONTOLOGY_SETUP_INSTRUCTIONS
    if mode is DataRuleAgentMode.DATA_GENERATION:
        return DATA_GENERATION_INSTRUCTIONS
    if mode is DataRuleAgentMode.DATA_REVISION:
        return DATA_REVISION_INSTRUCTIONS
    if mode is DataRuleAgentMode.RULE_GENERATION:
        return RULE_GENERATION_INSTRUCTIONS
    return RULE_REVISION_INSTRUCTIONS


def build_data_rule_prompt(mode: DataRuleAgentMode, payload: dict[str, Any]) -> str:
    """Build a mode-specific related data/rule extraction prompt."""

    return (
        f"{_mode_instructions(mode)}\n\n"
        f"Return DataRuleAgentOutput with mode='{mode.value}'. "
        "Only the Turtle string required by this mode is mandatory; explanatory fields may be flexible JSON. "
        "Do not repeat JSON keys. Omit irrelevant Turtle fields instead of setting them to null.\n\n"
        f"Input payload:\n{payload}"
    )


def _first_non_null_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a dict while preserving the first non-null value for duplicate keys."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key not in result or result[key] is None:
            result[key] = value
    return result


def _recover_output_from_model_error(error: ModelBehaviorError) -> DataRuleAgentOutput | None:
    """Recover structured output from an Agents SDK validation error when possible."""

    message = str(error)
    marker = "Invalid JSON when parsing "
    if marker not in message:
        return None
    start = message.find(marker) + len(marker)
    decoder = json.JSONDecoder(object_pairs_hook=_first_non_null_pairs)
    try:
        payload, _ = decoder.raw_decode(message[start:])
    except json.JSONDecodeError:
        return None
    return DataRuleAgentOutput.model_validate(payload)


def run_data_rule_agent(
    mode: DataRuleAgentMode,
    payload: dict[str, Any],
    model: str,
    runner: Callable[..., Any] | None = None,
) -> DataRuleAgentOutput:
    """Run the single related data/rule extraction agent in the requested mode."""

    ensure_openai_api_key()
    agent = build_data_rule_agent(model)
    prompt = build_data_rule_prompt(mode, payload)
    run = runner or Runner.run_sync
    try:
        result = run(agent, prompt)
    except ModelBehaviorError as error:
        recovered = _recover_output_from_model_error(error)
        if recovered is not None:
            return recovered
        raise
    final_output = getattr(result, "final_output", result)
    if isinstance(final_output, DataRuleAgentOutput):
        return final_output
    return DataRuleAgentOutput.model_validate(final_output)
