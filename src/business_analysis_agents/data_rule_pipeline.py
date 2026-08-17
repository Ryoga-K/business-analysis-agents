"""Pipeline for generating Data RDF and Rule RDF from scenario JSON."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from business_analysis_agents.agents.data_rule import run_data_rule_agent
from business_analysis_agents.models import (
    DataRuleAgentMode,
    DataRuleAgentOutput,
    OntologyValidationResult,
    RdfKind,
    ScenarioAgentOutput,
    WorkflowRdfValidationResult,
)
from business_analysis_agents.rdf_validation import (
    content_hash,
    validate_ontology_and_shapes,
    validate_rdf,
    validation_feedback,
)
from business_analysis_agents.workflow_pipeline import (
    load_scenario_output,
    write_json,
    write_text,
)


DEFAULT_MAX_ONTOLOGY_ITERATIONS = 3
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


def _run_ontology_setup(
    scenario: ScenarioAgentOutput,
    model: str,
    runner: Callable[..., Any] | None,
    max_iterations: int,
) -> tuple[DataRuleAgentOutput, OntologyValidationResult, OntologyValidationResult]:
    last_output: DataRuleAgentOutput | None = None
    last_data_validation: OntologyValidationResult | None = None
    last_rule_validation: OntologyValidationResult | None = None

    for iteration in range(1, max_iterations + 1):
        payload = {
            "scenario_json": scenario.model_dump(mode="json"),
            "iteration": iteration,
            "previous_data_validation": _ontology_validation_payload(last_data_validation)
            if last_data_validation
            else None,
            "previous_rule_validation": _ontology_validation_payload(last_rule_validation)
            if last_rule_validation
            else None,
        }
        output = run_data_rule_agent(
            DataRuleAgentMode.ONTOLOGY_SETUP,
            payload,
            model=model,
            runner=runner,
        )
        ontology_turtle = _require_text(output.ontology_turtle, "ontology_turtle")
        data_shapes = _require_text(output.data_shacl_turtle, "data_shacl_turtle")
        rule_shapes = _require_text(output.rule_shacl_turtle, "rule_shacl_turtle")
        data_validation = validate_ontology_and_shapes(ontology_turtle, data_shapes)
        rule_validation = validate_ontology_and_shapes(ontology_turtle, rule_shapes)
        last_output = output
        last_data_validation = data_validation
        last_rule_validation = rule_validation
        if data_validation.conforms and rule_validation.conforms:
            return output, data_validation, rule_validation

    assert (
        last_output is not None
        and last_data_validation is not None
        and last_rule_validation is not None
    )
    return last_output, last_data_validation, last_rule_validation


def _assert_fixed_hashes(
    ontology_turtle: str,
    data_shapes: str,
    rule_shapes: str,
    ontology_hash: str,
    data_shapes_hash: str,
    rule_shapes_hash: str,
) -> None:
    if content_hash(ontology_turtle) != ontology_hash:
        raise ValueError("Ontology changed after setup. Revisions must not change it.")
    if content_hash(data_shapes) != data_shapes_hash:
        raise ValueError("Data SHACL shapes changed after setup. Revisions must not change them.")
    if content_hash(rule_shapes) != rule_shapes_hash:
        raise ValueError("Rule SHACL shapes changed after setup. Revisions must not change them.")


def _generate_and_revise_data(
    scenario: ScenarioAgentOutput,
    model: str,
    runner: Callable[..., Any] | None,
    ontology_turtle: str,
    data_shapes: str,
    ontology_hash: str,
    data_shapes_hash: str,
    rule_shapes_hash: str,
    rule_shapes: str,
    max_iterations: int,
) -> tuple[str, str, DataRuleAgentOutput, WorkflowRdfValidationResult, list[dict[str, Any]]]:
    data_history: list[dict[str, Any]] = []
    output = run_data_rule_agent(
        DataRuleAgentMode.DATA_GENERATION,
        {
            "scenario_json": scenario.model_dump(mode="json"),
            "ontology_turtle": ontology_turtle,
            "data_shacl_turtle": data_shapes,
            "ontology_hash": ontology_hash,
            "data_shapes_hash": data_shapes_hash,
        },
        model=model,
        runner=runner,
    )
    data_turtle = _require_text(output.data_rdf_turtle, "data_rdf_turtle")
    initial_data_turtle = data_turtle
    validation = validate_rdf(
        data_turtle,
        ontology_turtle,
        data_shapes,
        rdf_kind=RdfKind.DATA,
        iteration=0,
    )
    final_output = output

    for iteration in range(1, max_iterations + 1):
        if validation.conforms:
            break
        _assert_fixed_hashes(
            ontology_turtle,
            data_shapes,
            rule_shapes,
            ontology_hash,
            data_shapes_hash,
            rule_shapes_hash,
        )
        output = run_data_rule_agent(
            DataRuleAgentMode.DATA_REVISION,
            {
                "scenario_json": scenario.model_dump(mode="json"),
                "ontology_turtle": ontology_turtle,
                "data_shacl_turtle": data_shapes,
                "previous_data_rdf": data_turtle,
                "validation_feedback": validation_feedback(validation),
                "previous_validation": _validation_payload(validation),
                "revision_history": data_history,
                "ontology_hash": ontology_hash,
                "data_shapes_hash": data_shapes_hash,
            },
            model=model,
            runner=runner,
        )
        data_turtle = _require_text(output.data_rdf_turtle, "data_rdf_turtle")
        validation = validate_rdf(
            data_turtle,
            ontology_turtle,
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
    scenario: ScenarioAgentOutput,
    model: str,
    runner: Callable[..., Any] | None,
    ontology_turtle: str,
    rule_shapes: str,
    data_turtle: str,
    ontology_hash: str,
    data_shapes_hash: str,
    rule_shapes_hash: str,
    data_shapes: str,
    max_iterations: int,
) -> tuple[str, str, DataRuleAgentOutput, WorkflowRdfValidationResult, list[dict[str, Any]]]:
    rule_history: list[dict[str, Any]] = []
    output = run_data_rule_agent(
        DataRuleAgentMode.RULE_GENERATION,
        {
            "scenario_json": scenario.model_dump(mode="json"),
            "validated_data_rdf": data_turtle,
            "ontology_turtle": ontology_turtle,
            "rule_shacl_turtle": rule_shapes,
            "ontology_hash": ontology_hash,
            "rule_shapes_hash": rule_shapes_hash,
        },
        model=model,
        runner=runner,
    )
    rule_turtle = _require_text(output.rule_rdf_turtle, "rule_rdf_turtle")
    initial_rule_turtle = rule_turtle
    validation = validate_rdf(
        rule_turtle,
        ontology_turtle,
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
            ontology_turtle,
            data_shapes,
            rule_shapes,
            ontology_hash,
            data_shapes_hash,
            rule_shapes_hash,
        )
        output = run_data_rule_agent(
            DataRuleAgentMode.RULE_REVISION,
            {
                "scenario_json": scenario.model_dump(mode="json"),
                "validated_data_rdf": data_turtle,
                "ontology_turtle": ontology_turtle,
                "rule_shacl_turtle": rule_shapes,
                "previous_rule_rdf": rule_turtle,
                "validation_feedback": validation_feedback(validation),
                "previous_validation": _validation_payload(validation),
                "revision_history": rule_history,
                "ontology_hash": ontology_hash,
                "rule_shapes_hash": rule_shapes_hash,
            },
            model=model,
            runner=runner,
        )
        rule_turtle = _require_text(output.rule_rdf_turtle, "rule_rdf_turtle")
        validation = validate_rdf(
            rule_turtle,
            ontology_turtle,
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
    output_dir: Path | str = "outputs/data_rule",
    max_ontology_iterations: int = DEFAULT_MAX_ONTOLOGY_ITERATIONS,
    max_data_iterations: int = DEFAULT_MAX_DATA_ITERATIONS,
    max_rule_iterations: int = DEFAULT_MAX_RULE_ITERATIONS,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run Data RDF and Rule RDF generation from a scenario JSON file."""

    scenario_path = Path(scenario_file)
    data_rule_dir = Path(output_dir)
    data_rule_dir.mkdir(parents=True, exist_ok=True)
    scenario = load_scenario_output(scenario_path)

    ontology_output, data_ontology_validation, rule_ontology_validation = _run_ontology_setup(
        scenario,
        model=model,
        runner=runner,
        max_iterations=max_ontology_iterations,
    )
    ontology_turtle = _require_text(ontology_output.ontology_turtle, "ontology_turtle")
    data_shapes = _require_text(ontology_output.data_shacl_turtle, "data_shacl_turtle")
    rule_shapes = _require_text(ontology_output.rule_shacl_turtle, "rule_shacl_turtle")
    ontology_hash = content_hash(ontology_turtle)
    data_shapes_hash = content_hash(data_shapes)
    rule_shapes_hash = content_hash(rule_shapes)

    write_text(data_rule_dir / "data_rule_ontology_v0_1.ttl", ontology_turtle)
    write_text(data_rule_dir / "data_shapes_v0_1.ttl", data_shapes)
    write_text(data_rule_dir / "rule_shapes_v0_1.ttl", rule_shapes)
    write_json(data_rule_dir / "data_rule_ontology_design.json", _output_payload(ontology_output))
    write_json(
        data_rule_dir / "data_ontology_validation.json",
        _ontology_validation_payload(data_ontology_validation),
    )
    write_json(
        data_rule_dir / "rule_ontology_validation.json",
        _ontology_validation_payload(rule_ontology_validation),
    )
    write_json(
        data_rule_dir / "data_rule_ontology_history.json",
        {
            "fixed": data_ontology_validation.conforms and rule_ontology_validation.conforms,
            "ontology_hash": ontology_hash,
            "data_shapes_hash": data_shapes_hash,
            "rule_shapes_hash": rule_shapes_hash,
        },
    )

    initial_data_turtle, data_turtle, data_output, data_validation, data_history = _generate_and_revise_data(
        scenario,
        model=model,
        runner=runner,
        ontology_turtle=ontology_turtle,
        data_shapes=data_shapes,
        ontology_hash=ontology_hash,
        data_shapes_hash=data_shapes_hash,
        rule_shapes_hash=rule_shapes_hash,
        rule_shapes=rule_shapes,
        max_iterations=max_data_iterations,
    )
    initial_rule_turtle, rule_turtle, rule_output, rule_validation, rule_history = _generate_and_revise_rule(
        scenario,
        model=model,
        runner=runner,
        ontology_turtle=ontology_turtle,
        rule_shapes=rule_shapes,
        data_turtle=data_turtle,
        ontology_hash=ontology_hash,
        data_shapes_hash=data_shapes_hash,
        rule_shapes_hash=rule_shapes_hash,
        data_shapes=data_shapes,
        max_iterations=max_rule_iterations,
    )

    final_status = (
        "completed"
        if data_validation.conforms
        and rule_validation.conforms
        and data_ontology_validation.conforms
        and rule_ontology_validation.conforms
        else "needs_review"
    )
    write_text(data_rule_dir / "data_initial.ttl", initial_data_turtle)
    write_text(data_rule_dir / "data_final.ttl", data_turtle)
    write_text(data_rule_dir / "rule_initial.ttl", initial_rule_turtle)
    write_text(data_rule_dir / "rule_final.ttl", rule_turtle)
    write_json(data_rule_dir / "data_agent_output.json", _output_payload(data_output))
    write_json(data_rule_dir / "rule_agent_output.json", _output_payload(rule_output))
    write_json(data_rule_dir / "data_validation.json", _validation_payload(data_validation))
    write_json(data_rule_dir / "rule_validation.json", _validation_payload(rule_validation))
    write_json(data_rule_dir / "data_revision_history.json", data_history)
    write_json(data_rule_dir / "rule_revision_history.json", rule_history)

    metadata = {
        "scenario_file": str(scenario_path),
        "execution_datetime": datetime.now().isoformat(),
        "model": model,
        "agent_name": "related_data_rule_agent",
        "ontology_version": ontology_output.ontology_version,
        "namespace_uri": ontology_output.namespace_uri,
        "ontology_hash": ontology_hash,
        "data_shapes_hash": data_shapes_hash,
        "rule_shapes_hash": rule_shapes_hash,
        "max_ontology_iterations": max_ontology_iterations,
        "max_data_iterations": max_data_iterations,
        "max_rule_iterations": max_rule_iterations,
        "final_status": final_status,
    }
    write_json(data_rule_dir / "data_rule_run_metadata.json", metadata)

    return {
        "output_dir": str(data_rule_dir),
        "final_status": final_status,
        "data_validation": _validation_payload(data_validation),
        "rule_validation": _validation_payload(rule_validation),
        "metadata": metadata,
    }
