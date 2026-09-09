"""Pipeline for generating and validating Data RDF and Rule RDF."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from business_analysis_agents.agents.data_rule import run_data_rule_agent
from business_analysis_agents.document_loader import load_pdf_document
from business_analysis_agents.fixed_resources import (
    DEFAULT_DATA_ONTOLOGY,
    DEFAULT_RULE_ONTOLOGY,
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


def _assert_fixed_resources(
    ontology_turtle: str,
    shacl_turtle: str,
    ontology_hash: str,
    shapes_hash: str,
    rdf_label: str,
) -> None:
    if content_hash(ontology_turtle) != ontology_hash:
        raise ValueError(f"{rdf_label} ontology changed. Revisions must not change it.")
    if content_hash(shacl_turtle) != shapes_hash:
        raise ValueError(
            f"{rdf_label} SHACL shapes changed after generation. "
            "Revisions must not change them."
        )


def _generate_and_revise_data(
    scenario_turtle: str,
    source_document: dict[str, Any],
    model: str,
    runner: Callable[..., Any] | None,
    data_ontology: str,
    data_ontology_hash: str,
    output_dir: Path,
    max_iterations: int,
) -> tuple[
    str,
    str,
    DataRuleAgentOutput,
    WorkflowRdfValidationResult,
    list[dict[str, Any]],
    str,
    OntologyValidationResult,
    str,
]:
    data_history: list[dict[str, Any]] = []
    output = run_data_rule_agent(
        DataRuleAgentMode.DATA_GENERATION,
        {
            "scenario_rdf_turtle": scenario_turtle,
            "source_document": source_document,
            "ontology_turtle": data_ontology,
            "ontology_hash": data_ontology_hash,
        },
        model=model,
        runner=runner,
    )
    data_turtle = _require_text(output.data_rdf_turtle, "data_rdf_turtle")
    initial_data_turtle = data_turtle
    shacl_output = run_data_rule_agent(
        DataRuleAgentMode.DATA_SHACL_GENERATION,
        {
            "data_rdf_raw": data_turtle,
            "ontology_turtle": data_ontology,
            "ontology_hash": data_ontology_hash,
        },
        model=model,
        runner=runner,
    )
    data_shapes = _require_text(
        shacl_output.data_shacl_turtle,
        "data_shacl_turtle",
    )
    write_text(output_dir / "data_shapes_generated.ttl", data_shapes)
    ontology_validation = validate_ontology_and_shapes(data_ontology, data_shapes)
    if not ontology_validation.conforms:
        raise ValueError(
            "Generated Data SHACL validation failed: "
            f"{_ontology_validation_payload(ontology_validation)}"
        )
    data_shapes_hash = content_hash(data_shapes)
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
        _assert_fixed_resources(
            data_ontology,
            data_shapes,
            data_ontology_hash,
            data_shapes_hash,
            "Data",
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
    return (
        initial_data_turtle,
        data_turtle,
        final_output,
        validation,
        data_history,
        data_shapes,
        ontology_validation,
        data_shapes_hash,
    )


def _generate_and_revise_rule(
    scenario_turtle: str,
    source_document: dict[str, Any],
    model: str,
    runner: Callable[..., Any] | None,
    rule_ontology: str,
    data_turtle: str,
    rule_ontology_hash: str,
    output_dir: Path,
    max_iterations: int,
) -> tuple[
    str,
    str,
    DataRuleAgentOutput,
    WorkflowRdfValidationResult,
    list[dict[str, Any]],
    str,
    OntologyValidationResult,
    str,
]:
    rule_history: list[dict[str, Any]] = []
    output = run_data_rule_agent(
        DataRuleAgentMode.RULE_GENERATION,
        {
            "scenario_rdf_turtle": scenario_turtle,
            "source_document": source_document,
            "validated_data_rdf": data_turtle,
            "ontology_turtle": rule_ontology,
            "ontology_hash": rule_ontology_hash,
        },
        model=model,
        runner=runner,
    )
    rule_turtle = _require_text(output.rule_rdf_turtle, "rule_rdf_turtle")
    initial_rule_turtle = rule_turtle
    shacl_output = run_data_rule_agent(
        DataRuleAgentMode.RULE_SHACL_GENERATION,
        {
            "rule_rdf_raw": rule_turtle,
            "ontology_turtle": rule_ontology,
            "ontology_hash": rule_ontology_hash,
        },
        model=model,
        runner=runner,
    )
    rule_shapes = _require_text(
        shacl_output.rule_shacl_turtle,
        "rule_shacl_turtle",
    )
    write_text(output_dir / "rule_shapes_generated.ttl", rule_shapes)
    ontology_validation = validate_ontology_and_shapes(rule_ontology, rule_shapes)
    if not ontology_validation.conforms:
        raise ValueError(
            "Generated Rule SHACL validation failed: "
            f"{_ontology_validation_payload(ontology_validation)}"
        )
    rule_shapes_hash = content_hash(rule_shapes)
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
        _assert_fixed_resources(
            rule_ontology,
            rule_shapes,
            rule_ontology_hash,
            rule_shapes_hash,
            "Rule",
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
    return (
        initial_rule_turtle,
        rule_turtle,
        final_output,
        validation,
        rule_history,
        rule_shapes,
        ontology_validation,
        rule_shapes_hash,
    )


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
) -> dict[str, Any]:
    """Generate Data/Rule RDF and one immutable SHACL graph for each RDF."""

    scenario_path = Path(scenario_file)
    data_rule_dir = Path(output_dir)
    data_rule_dir.mkdir(parents=True, exist_ok=True)
    (data_rule_dir / "data_shapes_generated.ttl").unlink(missing_ok=True)
    (data_rule_dir / "rule_shapes_generated.ttl").unlink(missing_ok=True)
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
    data_ontology_hash = content_hash(data_ontology)
    rule_ontology_hash = content_hash(rule_ontology)

    (
        initial_data_turtle,
        data_turtle,
        data_output,
        data_validation,
        data_history,
        data_shapes,
        data_ontology_validation,
        data_shapes_hash,
    ) = _generate_and_revise_data(
        scenario_turtle,
        source_document_payload,
        model=model,
        runner=runner,
        data_ontology=data_ontology,
        data_ontology_hash=data_ontology_hash,
        output_dir=data_rule_dir,
        max_iterations=max_data_iterations,
    )
    write_text(data_rule_dir / "data_final.ttl", data_turtle)
    write_json(data_rule_dir / "data_validation.json", _validation_payload(data_validation))
    write_json(data_rule_dir / "data_revision_history.json", data_history)

    (
        initial_rule_turtle,
        rule_turtle,
        rule_output,
        rule_validation,
        rule_history,
        rule_shapes,
        rule_ontology_validation,
        rule_shapes_hash,
    ) = _generate_and_revise_rule(
        scenario_turtle,
        source_document_payload,
        model=model,
        runner=runner,
        rule_ontology=rule_ontology,
        data_turtle=data_turtle,
        rule_ontology_hash=rule_ontology_hash,
        output_dir=data_rule_dir,
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
        "data_shapes_source": "ai_generated",
        "rule_shapes_source": "ai_generated",
        "data_shapes_file": str(data_rule_dir / "data_shapes_generated.ttl"),
        "rule_shapes_file": str(data_rule_dir / "rule_shapes_generated.ttl"),
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
