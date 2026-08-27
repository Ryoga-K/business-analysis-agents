"""Pipeline for generating Data RDF and Rule RDF from scenario JSON."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from business_analysis_agents.agents.data_rule import run_data_rule_agent
from business_analysis_agents.document_loader import load_pdf_document
from business_analysis_agents.fixed_resources import (
    DEFAULT_DATA_ONTOLOGY,
    DEFAULT_DATA_SHAPES,
    DEFAULT_RULE_ONTOLOGY,
    DEFAULT_RULE_SHAPES,
    load_fixed_turtle,
)
from business_analysis_agents.models import (
    DataRuleAgentMode,
    DataRuleAgentOutput,
    OntologyValidationResult,
    RdfKind,
    WorkflowRdfValidationResult,
)
from business_analysis_agents.rdf_validation import (
    content_hash,
    validate_ontology_and_shapes,
    validate_rdf,
    validation_feedback,
)
from business_analysis_agents.workflow_pipeline import (
    load_scenario_rdf,
    write_json,
    write_text,
)


DEFAULT_MAX_DATA_ITERATIONS = 3
DEFAULT_MAX_RULE_ITERATIONS = 3


def _output_payload(output: DataRuleAgentOutput) -> dict[str, Any]:
    return output.model_dump(mode="json")


def _validation_payload(validation: WorkflowRdfValidationResult) -> dict[str, Any]:
    data = validation.model_dump(mode="json")
    data["conforms"] = validation.conforms
    return data


def _ontology_validation_payload(validation: OntologyValidationResult) -> dict[str, Any]:
    data = validation.model_dump(mode="json")
    data["conforms"] = validation.conforms
    return data


def _require_text(value: str | None, field_name: str) -> str:
    if not value:
        raise ValueError(f"Data/Rule Agent output did not include {field_name}.")
    return value


def _assert_fixed_hashes(
    data_ontology: str,
    rule_ontology: str,
    data_shapes: str,
    rule_shapes: str,
    data_ontology_hash: str,
    rule_ontology_hash: str,
    data_shapes_hash: str,
    rule_shapes_hash: str,
) -> None:
    if content_hash(data_ontology) != data_ontology_hash:
        raise ValueError("Data ontology changed. Revisions must not change it.")
    if content_hash(rule_ontology) != rule_ontology_hash:
        raise ValueError("Rule ontology changed. Revisions must not change it.")
    if content_hash(data_shapes) != data_shapes_hash:
        raise ValueError("Data SHACL shapes changed after setup. Revisions must not change them.")
    if content_hash(rule_shapes) != rule_shapes_hash:
        raise ValueError("Rule SHACL shapes changed after setup. Revisions must not change them.")


def _generate_and_revise_data(
    scenario_turtle: str,
    source_document: dict[str, Any],
    model: str,
    runner: Callable[..., Any] | None,
    data_ontology: str,
    rule_ontology: str,
    data_shapes: str,
    data_ontology_hash: str,
    rule_ontology_hash: str,
    data_shapes_hash: str,
    rule_shapes_hash: str,
    rule_shapes: str,
    max_iterations: int,
) -> tuple[str, str, DataRuleAgentOutput, WorkflowRdfValidationResult, list[dict[str, Any]]]:
    data_history: list[dict[str, Any]] = []
    output = run_data_rule_agent(
        DataRuleAgentMode.DATA_GENERATION,
        {
            "scenario_rdf_turtle": scenario_turtle,
            "source_document": source_document,
            "ontology_turtle": data_ontology,
            "data_shacl_turtle": data_shapes,
            "ontology_hash": data_ontology_hash,
            "data_shapes_hash": data_shapes_hash,
        },
        model=model,
        runner=runner,
    )
    data_turtle = _require_text(output.data_rdf_turtle, "data_rdf_turtle")
    initial_data_turtle = data_turtle
    validation = validate_rdf(
        data_turtle,
        data_ontology,
        data_shapes,
        rdf_kind=RdfKind.DATA,
        iteration=0,
    )
    final_output = output

    for iteration in range(1, max_iterations + 1):
        if validation.conforms:
            break
        _assert_fixed_hashes(
            data_ontology,
            rule_ontology,
            data_shapes,
            rule_shapes,
            data_ontology_hash,
            rule_ontology_hash,
            data_shapes_hash,
            rule_shapes_hash,
        )
        output = run_data_rule_agent(
            DataRuleAgentMode.DATA_REVISION,
            {
                "scenario_rdf_turtle": scenario_turtle,
                "source_document": source_document,
                "ontology_turtle": data_ontology,
                "data_shacl_turtle": data_shapes,
                "previous_data_rdf": data_turtle,
                "validation_feedback": validation_feedback(validation),
                "previous_validation": _validation_payload(validation),
                "revision_history": data_history,
                "ontology_hash": data_ontology_hash,
                "data_shapes_hash": data_shapes_hash,
            },
            model=model,
            runner=runner,
        )
        data_turtle = _require_text(output.data_rdf_turtle, "data_rdf_turtle")
        validation = validate_rdf(
            data_turtle,
            data_ontology,
            data_shapes,
            rdf_kind=RdfKind.DATA,
            iteration=iteration,
        )
        final_output = output
        data_history.append(
            {
                "iteration": iteration,
                "output": _output_payload(output),
                "validation": _validation_payload(validation),
            }
        )
    return initial_data_turtle, data_turtle, final_output, validation, data_history


def _generate_and_revise_rule(
    scenario_turtle: str,
    source_document: dict[str, Any],
    model: str,
    runner: Callable[..., Any] | None,
    rule_ontology: str,
    data_ontology: str,
    rule_shapes: str,
    data_turtle: str,
    rule_ontology_hash: str,
    data_ontology_hash: str,
    data_shapes_hash: str,
    rule_shapes_hash: str,
    data_shapes: str,
    max_iterations: int,
) -> tuple[str, str, DataRuleAgentOutput, WorkflowRdfValidationResult, list[dict[str, Any]]]:
    rule_history: list[dict[str, Any]] = []
    output = run_data_rule_agent(
        DataRuleAgentMode.RULE_GENERATION,
        {
            "scenario_rdf_turtle": scenario_turtle,
            "source_document": source_document,
            "validated_data_rdf": data_turtle,
            "ontology_turtle": rule_ontology,
            "rule_shacl_turtle": rule_shapes,
            "ontology_hash": rule_ontology_hash,
            "rule_shapes_hash": rule_shapes_hash,
        },
        model=model,
        runner=runner,
    )
    rule_turtle = _require_text(output.rule_rdf_turtle, "rule_rdf_turtle")
    initial_rule_turtle = rule_turtle
    validation = validate_rdf(
        rule_turtle,
        rule_ontology,
        rule_shapes,
        rdf_kind=RdfKind.RULE,
        additional_data_turtle=data_turtle,
        iteration=0,
    )
    final_output = output

    for iteration in range(1, max_iterations + 1):
        if validation.conforms:
            break
        _assert_fixed_hashes(
            data_ontology,
            rule_ontology,
            data_shapes,
            rule_shapes,
            data_ontology_hash,
            rule_ontology_hash,
            data_shapes_hash,
            rule_shapes_hash,
        )
        output = run_data_rule_agent(
            DataRuleAgentMode.RULE_REVISION,
            {
                "scenario_rdf_turtle": scenario_turtle,
                "source_document": source_document,
                "validated_data_rdf": data_turtle,
                "ontology_turtle": rule_ontology,
                "rule_shacl_turtle": rule_shapes,
                "previous_rule_rdf": rule_turtle,
                "validation_feedback": validation_feedback(validation),
                "previous_validation": _validation_payload(validation),
                "revision_history": rule_history,
                "ontology_hash": rule_ontology_hash,
                "rule_shapes_hash": rule_shapes_hash,
            },
            model=model,
            runner=runner,
        )
        rule_turtle = _require_text(output.rule_rdf_turtle, "rule_rdf_turtle")
        validation = validate_rdf(
            rule_turtle,
            rule_ontology,
            rule_shapes,
            rdf_kind=RdfKind.RULE,
            additional_data_turtle=data_turtle,
            iteration=iteration,
        )
        final_output = output
        rule_history.append(
            {
                "iteration": iteration,
                "output": _output_payload(output),
                "validation": _validation_payload(validation),
            }
        )
    return initial_rule_turtle, rule_turtle, final_output, validation, rule_history


def run_data_rule_pipeline(
    scenario_file: Path | str,
    model: str,
    pdf_file: Path | str,
    output_dir: Path | str = "outputs/data_rule",
    max_data_iterations: int = DEFAULT_MAX_DATA_ITERATIONS,
    max_rule_iterations: int = DEFAULT_MAX_RULE_ITERATIONS,
    runner: Callable[..., Any] | None = None,
    data_ontology_file: Path | str = DEFAULT_DATA_ONTOLOGY,
    rule_ontology_file: Path | str = DEFAULT_RULE_ONTOLOGY,
    data_shapes_file: Path | str = DEFAULT_DATA_SHAPES,
    rule_shapes_file: Path | str = DEFAULT_RULE_SHAPES,
) -> dict[str, Any]:
    """Run Data RDF and Rule RDF generation from a scenario JSON file."""

    scenario_path = Path(scenario_file)
    data_rule_dir = Path(output_dir)
    data_rule_dir.mkdir(parents=True, exist_ok=True)
    scenario_turtle = load_scenario_rdf(scenario_path)
    pdf_path = Path(pdf_file)
    source_document = load_pdf_document(pdf_path)
    source_document_payload = source_document.model_dump(
        mode="json",
        exclude={"text"},
    )

    data_ontology_path, data_ontology = load_fixed_turtle(
        data_ontology_file, "Data ontology"
    )
    rule_ontology_path, rule_ontology = load_fixed_turtle(
        rule_ontology_file, "Rule ontology"
    )
    data_shapes_path, data_shapes = load_fixed_turtle(data_shapes_file, "Data SHACL")
    rule_shapes_path, rule_shapes = load_fixed_turtle(rule_shapes_file, "Rule SHACL")
    data_ontology_validation = validate_ontology_and_shapes(data_ontology, data_shapes)
    rule_ontology_validation = validate_ontology_and_shapes(rule_ontology, rule_shapes)
    if not data_ontology_validation.conforms:
        raise ValueError(
            "Fixed Data ontology/SHACL validation failed: "
            f"{_ontology_validation_payload(data_ontology_validation)}"
        )
    if not rule_ontology_validation.conforms:
        raise ValueError(
            "Fixed Rule ontology/SHACL validation failed: "
            f"{_ontology_validation_payload(rule_ontology_validation)}"
        )

    data_ontology_hash = content_hash(data_ontology)
    rule_ontology_hash = content_hash(rule_ontology)
    data_shapes_hash = content_hash(data_shapes)
    rule_shapes_hash = content_hash(rule_shapes)

    initial_data_turtle, data_turtle, data_output, data_validation, data_history = _generate_and_revise_data(
        scenario_turtle,
        source_document_payload,
        model=model,
        runner=runner,
        data_ontology=data_ontology,
        rule_ontology=rule_ontology,
        data_shapes=data_shapes,
        data_ontology_hash=data_ontology_hash,
        rule_ontology_hash=rule_ontology_hash,
        data_shapes_hash=data_shapes_hash,
        rule_shapes_hash=rule_shapes_hash,
        rule_shapes=rule_shapes,
        max_iterations=max_data_iterations,
    )
    write_text(data_rule_dir / "data_final.ttl", data_turtle)
    write_json(data_rule_dir / "data_validation.json", _validation_payload(data_validation))
    write_json(data_rule_dir / "data_revision_history.json", data_history)

    initial_rule_turtle, rule_turtle, rule_output, rule_validation, rule_history = _generate_and_revise_rule(
        scenario_turtle,
        source_document_payload,
        model=model,
        runner=runner,
        rule_ontology=rule_ontology,
        data_ontology=data_ontology,
        rule_shapes=rule_shapes,
        data_turtle=data_turtle,
        rule_ontology_hash=rule_ontology_hash,
        data_ontology_hash=data_ontology_hash,
        data_shapes_hash=data_shapes_hash,
        rule_shapes_hash=rule_shapes_hash,
        data_shapes=data_shapes,
        max_iterations=max_rule_iterations,
    )
    write_text(data_rule_dir / "rule_final.ttl", rule_turtle)
    write_json(data_rule_dir / "rule_validation.json", _validation_payload(rule_validation))
    write_json(data_rule_dir / "rule_revision_history.json", rule_history)

    final_status = (
        "completed"
        if data_validation.conforms
        and rule_validation.conforms
        and data_ontology_validation.conforms
        and rule_ontology_validation.conforms
        else "needs_review"
    )
    metadata = {
        "scenario_file": str(scenario_path),
        "pdf_file": str(pdf_path),
        "execution_datetime": datetime.now().isoformat(),
        "model": model,
        "agent_name": "related_data_rule_agent",
        "data_ontology_source_file": str(data_ontology_path),
        "rule_ontology_source_file": str(rule_ontology_path),
        "data_shapes_source_file": str(data_shapes_path),
        "rule_shapes_source_file": str(rule_shapes_path),
        "data_ontology_hash": data_ontology_hash,
        "rule_ontology_hash": rule_ontology_hash,
        "data_shapes_hash": data_shapes_hash,
        "rule_shapes_hash": rule_shapes_hash,
        "max_data_iterations": max_data_iterations,
        "max_rule_iterations": max_rule_iterations,
        "final_status": final_status,
    }
    return {
        "output_dir": str(data_rule_dir),
        "final_status": final_status,
        "data_validation": _validation_payload(data_validation),
        "rule_validation": _validation_payload(rule_validation),
        "metadata": metadata,
    }
