"""Targeted RDF revision after Cross-SHACL consistency findings."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from business_analysis_agents.agents.data_rule import run_data_rule_agent
from business_analysis_agents.agents.workflow import run_workflow_agent
from business_analysis_agents.config import MAX_REVISION_ITERATIONS
from business_analysis_agents.document_loader import load_pdf_document
from business_analysis_agents.fixed_resources import load_fixed_turtle
from business_analysis_agents.models import (
    AgentName,
    DataRuleAgentMode,
    DataRuleAgentOutput,
    RdfKind,
    SelfReviewResult,
    SelfReviewRunStatus,
    WorkflowAgentMode,
    WorkflowAgentOutput,
    WorkflowRdfValidationResult,
)
from business_analysis_agents.rdf_validation import (
    content_hash,
    validate_ontology_and_shapes,
    validate_rdf,
    validate_workflow_rdf,
    validation_feedback,
)
from business_analysis_agents.progress import (
    ProgressReporter,
    ProgressStatus,
    report_progress,
)
from business_analysis_agents.self_review import run_self_review_loop
from business_analysis_agents.workflow_pipeline import (
    load_scenario_rdf,
    write_json,
    write_text,
)


def _load_text(path: Path | str, label: str) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Required {label} was not found: {file_path}")
    text = file_path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"Required {label} is empty: {file_path}")
    return text


def _load_history(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid revision history JSON: {path}: {error}") from error
    if not isinstance(payload, list):
        raise ValueError(f"Revision history must be a JSON array: {path}")
    return payload


def _validation_payload(
    validation: WorkflowRdfValidationResult,
) -> dict[str, Any]:
    payload = validation.model_dump(mode="json")
    payload["conforms"] = validation.conforms
    return payload


def _assert_fixed_resources(
    ontology_turtle: str,
    shapes_turtle: str,
    ontology_hash: str,
    shapes_hash: str,
) -> None:
    if content_hash(ontology_turtle) != ontology_hash:
        raise ValueError("Fixed ontology changed during targeted revision.")
    if content_hash(shapes_turtle) != shapes_hash:
        raise ValueError("Individual SHACL changed during targeted revision.")


def _require_revised_rdf(output: Any, field_name: str) -> str:
    value = getattr(output, field_name, None)
    if not value:
        raise ValueError(f"Targeted revision did not include {field_name}.")
    return value


def _require_self_review(
    output: WorkflowAgentOutput | DataRuleAgentOutput,
) -> SelfReviewResult:
    result = output.self_review_result
    if result is None:
        raise ValueError("Targeted Self-Review did not include self_review_result.")
    turtle_fields = (
        "workflow_rdf_turtle",
        "workflow_shacl_turtle",
        "data_rdf_turtle",
        "rule_rdf_turtle",
        "data_shacl_turtle",
        "rule_shacl_turtle",
    )
    if any(getattr(output, field, None) for field in turtle_fields):
        raise ValueError("Targeted Self-Review must not generate RDF or SHACL Turtle.")
    return result


def run_targeted_rdf_revision(
    *,
    target_agent: AgentName,
    repair_bundle: dict[str, Any],
    consistency_iteration: int,
    scenario_file: Path | str,
    pdf_file: Path | str,
    model: str,
    workflow_file: Path | str,
    data_file: Path | str,
    rule_file: Path | str,
    workflow_shapes_file: Path | str,
    data_shapes_file: Path | str,
    rule_shapes_file: Path | str,
    workflow_ontology_file: Path | str,
    data_ontology_file: Path | str,
    rule_ontology_file: Path | str,
    workflow_validation_file: Path | str,
    data_validation_file: Path | str,
    rule_validation_file: Path | str,
    workflow_revision_history_file: Path | str,
    data_revision_history_file: Path | str,
    rule_revision_history_file: Path | str,
    workflow_self_review_file: Path | str,
    data_self_review_file: Path | str,
    rule_self_review_file: Path | str,
    workflow_runner: Callable[..., Any] | None = None,
    data_rule_runner: Callable[..., Any] | None = None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Revise one RDF, then run its fixed individual SHACL and Self-Review."""

    if target_agent not in {AgentName.WORKFLOW, AgentName.DATA, AgentName.RULE}:
        raise ValueError(f"Unsupported targeted revision agent: {target_agent.value}")

    scenario_turtle = load_scenario_rdf(scenario_file)
    source_document = load_pdf_document(pdf_file).model_dump(
        mode="json",
        exclude={"text"},
    )
    rdf_turtles = {
        AgentName.WORKFLOW: _load_text(workflow_file, "Workflow RDF"),
        AgentName.DATA: _load_text(data_file, "Data RDF"),
        AgentName.RULE: _load_text(rule_file, "Rule RDF"),
    }
    rdf_files = {
        AgentName.WORKFLOW: Path(workflow_file),
        AgentName.DATA: Path(data_file),
        AgentName.RULE: Path(rule_file),
    }
    shape_files = {
        AgentName.WORKFLOW: Path(workflow_shapes_file),
        AgentName.DATA: Path(data_shapes_file),
        AgentName.RULE: Path(rule_shapes_file),
    }
    ontology_files = {
        AgentName.WORKFLOW: workflow_ontology_file,
        AgentName.DATA: data_ontology_file,
        AgentName.RULE: rule_ontology_file,
    }
    validation_files = {
        AgentName.WORKFLOW: Path(workflow_validation_file),
        AgentName.DATA: Path(data_validation_file),
        AgentName.RULE: Path(rule_validation_file),
    }
    history_files = {
        AgentName.WORKFLOW: Path(workflow_revision_history_file),
        AgentName.DATA: Path(data_revision_history_file),
        AgentName.RULE: Path(rule_revision_history_file),
    }
    self_review_files = {
        AgentName.WORKFLOW: Path(workflow_self_review_file),
        AgentName.DATA: Path(data_self_review_file),
        AgentName.RULE: Path(rule_self_review_file),
    }

    rdf_kind = RdfKind(target_agent.value)
    current_rdf = rdf_turtles[target_agent]
    shapes_turtle = _load_text(shape_files[target_agent], "individual SHACL")
    _, ontology_turtle = load_fixed_turtle(
        ontology_files[target_agent],
        f"{target_agent.value} ontology",
    )
    ontology_validation = validate_ontology_and_shapes(
        ontology_turtle,
        shapes_turtle,
    )
    if not ontology_validation.conforms:
        raise ValueError(
            f"Existing {target_agent.value} ontology/SHACL is invalid: "
            f"{ontology_validation.model_dump(mode='json')}"
        )
    ontology_hash = content_hash(ontology_turtle)
    shapes_hash = content_hash(shapes_turtle)
    revision_history = _load_history(history_files[target_agent])
    is_human_review_revision = repair_bundle.get("source") == "human_review"
    revision_phase = (
        "human_review_revision"
        if is_human_review_revision
        else "cross_consistency_revision"
    )
    repair_phase = (
        "human_review_shacl_repair"
        if is_human_review_revision
        else "cross_revision_shacl_repair"
    )

    def assert_fixed() -> None:
        _assert_fixed_resources(
            ontology_turtle,
            shapes_turtle,
            ontology_hash,
            shapes_hash,
        )

    def validate_target(
        rdf_turtle: str,
        iteration: int,
    ) -> WorkflowRdfValidationResult:
        if target_agent is AgentName.WORKFLOW:
            return validate_workflow_rdf(
                rdf_turtle,
                ontology_turtle,
                shapes_turtle,
                iteration=iteration,
            )
        return validate_rdf(
            rdf_turtle,
            ontology_turtle,
            shapes_turtle,
            rdf_kind=rdf_kind,
            additional_data_turtle=(
                rdf_turtles[AgentName.DATA]
                if target_agent is AgentName.RULE
                else None
            ),
            iteration=iteration,
        )

    def revision_payload(
        rdf_turtle: str,
        validation: WorkflowRdfValidationResult,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scenario_rdf_turtle": scenario_turtle,
            "source_document": source_document,
            "ontology_turtle": ontology_turtle,
            "related_rdfs": {
                agent.value: turtle for agent, turtle in rdf_turtles.items()
            },
            "validation_feedback": validation_feedback(validation),
            "previous_validation": _validation_payload(validation),
            "revision_history": revision_history,
            "ontology_hash": ontology_hash,
        }
        if is_human_review_revision:
            payload["human_review_revision"] = repair_bundle
        else:
            payload["cross_consistency_revision"] = repair_bundle
        if target_agent is AgentName.WORKFLOW:
            payload.update(
                {
                    "shacl_turtle": shapes_turtle,
                    "previous_workflow_rdf": rdf_turtle,
                    "shapes_hash": shapes_hash,
                }
            )
        elif target_agent is AgentName.DATA:
            payload.update(
                {
                    "data_shacl_turtle": shapes_turtle,
                    "previous_data_rdf": rdf_turtle,
                    "data_shapes_hash": shapes_hash,
                }
            )
        else:
            payload.update(
                {
                    "validated_data_rdf": rdf_turtles[AgentName.DATA],
                    "rule_shacl_turtle": shapes_turtle,
                    "previous_rule_rdf": rdf_turtle,
                    "rule_shapes_hash": shapes_hash,
                }
            )
        return payload

    def run_revision(
        rdf_turtle: str,
        validation: WorkflowRdfValidationResult,
    ) -> tuple[str, WorkflowAgentOutput | DataRuleAgentOutput]:
        assert_fixed()
        payload = revision_payload(rdf_turtle, validation)
        if target_agent is AgentName.WORKFLOW:
            output = run_workflow_agent(
                WorkflowAgentMode.WORKFLOW_REVISION,
                payload,
                model=model,
                runner=workflow_runner,
            )
            return _require_revised_rdf(output, "workflow_rdf_turtle"), output
        mode = (
            DataRuleAgentMode.DATA_REVISION
            if target_agent is AgentName.DATA
            else DataRuleAgentMode.RULE_REVISION
        )
        output = run_data_rule_agent(
            mode,
            payload,
            model=model,
            runner=data_rule_runner,
        )
        field_name = (
            "data_rdf_turtle"
            if target_agent is AgentName.DATA
            else "rule_rdf_turtle"
        )
        return _require_revised_rdf(output, field_name), output

    validation = validate_target(current_rdf, len(revision_history))
    current_rdf, revision_output = run_revision(current_rdf, validation)
    revision_iteration = len(revision_history) + 1
    validation = validate_target(current_rdf, revision_iteration)
    report_progress(
        progress,
        phase=target_agent.value,
        step="shacl_validation",
        status=ProgressStatus.PASSED if validation.conforms else ProgressStatus.FAILED,
        message=(
            f"{target_agent.value.title()} SHACL re-validation passed"
            if validation.conforms
            else (
                f"{target_agent.value.title()} SHACL re-validation found "
                "violations"
            )
        ),
    )
    revision_history.append(
        {
            "iteration": revision_iteration,
            "phase": revision_phase,
            "consistency_iteration": consistency_iteration,
            revision_phase: repair_bundle,
            "output": revision_output.model_dump(mode="json"),
            "validation": _validation_payload(validation),
        }
    )

    for repair_iteration in range(1, MAX_REVISION_ITERATIONS + 1):
        if validation.conforms:
            break
        report_progress(
            progress,
            phase=target_agent.value,
            step="shacl_revision",
            status=ProgressStatus.REVISION,
            message=f"{target_agent.value.title()} SHACL revision",
            iteration=repair_iteration,
            max_iterations=MAX_REVISION_ITERATIONS,
        )
        current_rdf, revision_output = run_revision(current_rdf, validation)
        revision_iteration = len(revision_history) + 1
        validation = validate_target(current_rdf, revision_iteration)
        report_progress(
            progress,
            phase=target_agent.value,
            step="shacl_validation",
            status=(
                ProgressStatus.PASSED
                if validation.conforms
                else ProgressStatus.FAILED
            ),
            message=(
                f"{target_agent.value.title()} SHACL re-validation passed"
                if validation.conforms
                else f"{target_agent.value.title()} SHACL violations remain"
            ),
            iteration=repair_iteration,
            max_iterations=MAX_REVISION_ITERATIONS,
        )
        revision_history.append(
            {
                "iteration": revision_iteration,
                "phase": repair_phase,
                "consistency_iteration": consistency_iteration,
                revision_phase: repair_bundle,
                "output": revision_output.model_dump(mode="json"),
                "validation": _validation_payload(validation),
            }
        )

    def review_rdf(rdf_turtle: str, review_iteration: int) -> SelfReviewResult:
        assert_fixed()
        payload: dict[str, Any] = {
            "scenario_rdf_turtle": scenario_turtle,
            "source_document": source_document,
            "ontology_turtle": ontology_turtle,
            "current_validation": _validation_payload(validation),
            "self_review_iteration": review_iteration,
            "ontology_hash": ontology_hash,
        }
        if is_human_review_revision:
            payload["human_review_revision"] = repair_bundle
        if target_agent is AgentName.WORKFLOW:
            payload.update(
                {
                    "workflow_shacl_turtle": shapes_turtle,
                    "current_workflow_rdf": rdf_turtle,
                    "shapes_hash": shapes_hash,
                }
            )
            output = run_workflow_agent(
                WorkflowAgentMode.WORKFLOW_SELF_REVIEW,
                payload,
                model=model,
                runner=workflow_runner,
            )
        else:
            prefix = "data" if target_agent is AgentName.DATA else "rule"
            payload.update(
                {
                    f"{prefix}_shacl_turtle": shapes_turtle,
                    f"current_{prefix}_rdf": rdf_turtle,
                    f"{prefix}_shapes_hash": shapes_hash,
                }
            )
            if target_agent is AgentName.RULE:
                payload["validated_data_rdf"] = rdf_turtles[AgentName.DATA]
            mode = (
                DataRuleAgentMode.DATA_SELF_REVIEW
                if target_agent is AgentName.DATA
                else DataRuleAgentMode.RULE_SELF_REVIEW
            )
            output = run_data_rule_agent(
                mode,
                payload,
                model=model,
                runner=data_rule_runner,
            )
        return _require_self_review(output)

    def revise_from_self_review(
        rdf_turtle: str,
        self_review_result: SelfReviewResult,
        current_validation: WorkflowRdfValidationResult,
        _iteration: int,
    ) -> tuple[str, dict[str, Any], WorkflowAgentOutput | DataRuleAgentOutput]:
        payload = revision_payload(rdf_turtle, current_validation)
        payload["self_review_result"] = self_review_result.model_dump(mode="json")
        assert_fixed()
        if target_agent is AgentName.WORKFLOW:
            output = run_workflow_agent(
                WorkflowAgentMode.WORKFLOW_REVISION,
                payload,
                model=model,
                runner=workflow_runner,
            )
            revised = _require_revised_rdf(output, "workflow_rdf_turtle")
        else:
            mode = (
                DataRuleAgentMode.DATA_REVISION
                if target_agent is AgentName.DATA
                else DataRuleAgentMode.RULE_REVISION
            )
            output = run_data_rule_agent(
                mode,
                payload,
                model=model,
                runner=data_rule_runner,
            )
            field = (
                "data_rdf_turtle"
                if target_agent is AgentName.DATA
                else "rule_rdf_turtle"
            )
            revised = _require_revised_rdf(output, field)
        return revised, output.model_dump(mode="json"), output

    current_rdf, validation, self_review, _ = run_self_review_loop(
        rdf_turtle=current_rdf,
        validation=validation,
        rdf_kind=rdf_kind,
        reviewer_agent=target_agent,
        revision_history=revision_history,
        review_rdf=review_rdf,
        revise_rdf=revise_from_self_review,
        validate_rdf=validate_target,
        serialize_validation=_validation_payload,
        assert_fixed_resources=assert_fixed,
        progress=progress,
        phase=target_agent.value,
    )

    write_text(rdf_files[target_agent], current_rdf)
    write_json(validation_files[target_agent], _validation_payload(validation))
    write_json(history_files[target_agent], revision_history)
    write_json(
        self_review_files[target_agent],
        self_review.model_dump(mode="json"),
    )
    final_status = (
        "completed"
        if validation.conforms and self_review.status is SelfReviewRunStatus.PASSED
        else "needs_review"
    )
    return {
        "target_agent": target_agent.value,
        "final_status": final_status,
        "rdf_file": str(rdf_files[target_agent]),
        "rdf_hash": content_hash(current_rdf),
        "individual_shapes_hash": shapes_hash,
        "validation": _validation_payload(validation),
        "self_review": self_review.model_dump(mode="json"),
        "revision_history_file": str(history_files[target_agent]),
        "self_review_file": str(self_review_files[target_agent]),
    }
