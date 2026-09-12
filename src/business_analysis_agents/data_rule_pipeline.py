"""Pipeline for generating and validating Data RDF and Rule RDF."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from business_analysis_agents.agents.data_rule import run_data_rule_agent
from business_analysis_agents.config import MAX_REVISION_ITERATIONS
from business_analysis_agents.document_loader import load_pdf_document
from business_analysis_agents.fixed_resources import (
    DEFAULT_DATA_ONTOLOGY,
    DEFAULT_RULE_ONTOLOGY,
    load_fixed_turtle,
)
from business_analysis_agents.models import (
    AgentName,
    DataRuleAgentMode,
    DataRuleAgentOutput,
    OntologyValidationResult,
    RdfKind,
    SelfReviewHistory,
    SelfReviewResult,
    SelfReviewRunStatus,
    WorkflowRdfValidationResult,
)
from business_analysis_agents.rdf_validation import (
    content_hash,
    validate_ontology_and_shapes,
    validate_rdf,
    validation_feedback,
)
from business_analysis_agents.progress import (
    ProgressReporter,
    ProgressStatus,
    progress_operation,
    report_progress,
)
from business_analysis_agents.self_review import run_self_review_loop
from business_analysis_agents.workflow_pipeline import (
    load_scenario_rdf,
    write_json,
    write_text,
)

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


def _require_self_review(output: DataRuleAgentOutput) -> SelfReviewResult:
    result = output.self_review_result
    if result is None:
        raise ValueError("Data/Rule Self-Review output did not include self_review_result.")
    if any(
        (
            output.data_rdf_turtle,
            output.rule_rdf_turtle,
            output.data_shacl_turtle,
            output.rule_shacl_turtle,
        )
    ):
        raise ValueError("Data/Rule Self-Review must not generate RDF or SHACL Turtle.")
    return result


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
    progress: ProgressReporter | None,
) -> tuple[
    str,
    str,
    DataRuleAgentOutput,
    WorkflowRdfValidationResult,
    list[dict[str, Any]],
    str,
    OntologyValidationResult,
    str,
    SelfReviewHistory,
]:
    data_history: list[dict[str, Any]] = []
    with progress_operation(
        progress,
        phase="data",
        step="rdf_generation",
        message="Data RDF generation",
        completed_message="Data RDF generated",
    ):
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
    with progress_operation(
        progress,
        phase="data",
        step="shacl_generation",
        message="Data SHACL generation",
        completed_message="Data SHACL generated",
    ):
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
    report_progress(
        progress,
        phase="data",
        step="shacl_validation",
        status=ProgressStatus.PASSED if validation.conforms else ProgressStatus.FAILED,
        message=(
            "Data SHACL validation passed"
            if validation.conforms
            else "Data SHACL validation found violations"
        ),
    )
    final_output = output

    for iteration in range(1, MAX_REVISION_ITERATIONS + 1):
        if validation.conforms:
            break
        report_progress(
            progress,
            phase="data",
            step="shacl_revision",
            status=ProgressStatus.REVISION,
            message="Data SHACL revision",
            iteration=iteration,
            max_iterations=MAX_REVISION_ITERATIONS,
        )
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
        report_progress(
            progress,
            phase="data",
            step="shacl_validation",
            status=(
                ProgressStatus.PASSED
                if validation.conforms
                else ProgressStatus.FAILED
            ),
            message=(
                "Data SHACL validation passed"
                if validation.conforms
                else "Data SHACL violations remain"
            ),
            iteration=iteration,
            max_iterations=MAX_REVISION_ITERATIONS,
        )
        final_output = output
        data_history.append(
            {
                "iteration": iteration,
                "phase": "shacl_revision",
                "output": _output_payload(output),
                "validation": _validation_payload(validation),
            }
        )

    def assert_fixed_resources() -> None:
        _assert_fixed_resources(
            data_ontology,
            data_shapes,
            data_ontology_hash,
            data_shapes_hash,
            "Data",
        )

    def review_rdf(current_rdf: str, review_iteration: int) -> SelfReviewResult:
        review_output = run_data_rule_agent(
            DataRuleAgentMode.DATA_SELF_REVIEW,
            {
                "scenario_rdf_turtle": scenario_turtle,
                "source_document": source_document,
                "ontology_turtle": data_ontology,
                "data_shacl_turtle": data_shapes,
                "current_data_rdf": current_rdf,
                "self_review_iteration": review_iteration,
                "ontology_hash": data_ontology_hash,
                "data_shapes_hash": data_shapes_hash,
            },
            model=model,
            runner=runner,
        )
        return _require_self_review(review_output)

    def revise_from_self_review(
        current_rdf: str,
        self_review_result: SelfReviewResult,
        current_validation: WorkflowRdfValidationResult,
        iteration: int,
    ) -> tuple[str, dict[str, Any], DataRuleAgentOutput]:
        revision_output = run_data_rule_agent(
            DataRuleAgentMode.DATA_REVISION,
            {
                "scenario_rdf_turtle": scenario_turtle,
                "source_document": source_document,
                "ontology_turtle": data_ontology,
                "data_shacl_turtle": data_shapes,
                "previous_data_rdf": current_rdf,
                "self_review_result": self_review_result.model_dump(mode="json"),
                "validation_feedback": validation_feedback(current_validation),
                "previous_validation": _validation_payload(current_validation),
                "revision_history": data_history,
                "ontology_hash": data_ontology_hash,
                "data_shapes_hash": data_shapes_hash,
            },
            model=model,
            runner=runner,
        )
        revised_rdf = _require_text(revision_output.data_rdf_turtle, "data_rdf_turtle")
        return revised_rdf, _output_payload(revision_output), revision_output

    def validate_revised_rdf(
        revised_rdf: str,
        iteration: int,
    ) -> WorkflowRdfValidationResult:
        return validate_rdf(
            revised_rdf,
            data_ontology,
            data_shapes,
            rdf_kind=RdfKind.DATA,
            iteration=iteration,
        )

    data_turtle, validation, self_review_history, revision_output = (
        run_self_review_loop(
            rdf_turtle=data_turtle,
            validation=validation,
            rdf_kind=RdfKind.DATA,
            reviewer_agent=AgentName.DATA,
            revision_history=data_history,
            review_rdf=review_rdf,
            revise_rdf=revise_from_self_review,
            validate_rdf=validate_revised_rdf,
            serialize_validation=_validation_payload,
            assert_fixed_resources=assert_fixed_resources,
            progress=progress,
            phase="data",
        )
    )
    if revision_output is not None:
        final_output = revision_output
    return (
        initial_data_turtle,
        data_turtle,
        final_output,
        validation,
        data_history,
        data_shapes,
        ontology_validation,
        data_shapes_hash,
        self_review_history,
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
    progress: ProgressReporter | None,
) -> tuple[
    str,
    str,
    DataRuleAgentOutput,
    WorkflowRdfValidationResult,
    list[dict[str, Any]],
    str,
    OntologyValidationResult,
    str,
    SelfReviewHistory,
]:
    rule_history: list[dict[str, Any]] = []
    with progress_operation(
        progress,
        phase="rule",
        step="rdf_generation",
        message="Rule RDF generation",
        completed_message="Rule RDF generated",
    ):
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
    with progress_operation(
        progress,
        phase="rule",
        step="shacl_generation",
        message="Rule SHACL generation",
        completed_message="Rule SHACL generated",
    ):
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
    report_progress(
        progress,
        phase="rule",
        step="shacl_validation",
        status=ProgressStatus.PASSED if validation.conforms else ProgressStatus.FAILED,
        message=(
            "Rule SHACL validation passed"
            if validation.conforms
            else "Rule SHACL validation found violations"
        ),
    )
    final_output = output

    for iteration in range(1, MAX_REVISION_ITERATIONS + 1):
        if validation.conforms:
            break
        report_progress(
            progress,
            phase="rule",
            step="shacl_revision",
            status=ProgressStatus.REVISION,
            message="Rule SHACL revision",
            iteration=iteration,
            max_iterations=MAX_REVISION_ITERATIONS,
        )
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
        report_progress(
            progress,
            phase="rule",
            step="shacl_validation",
            status=(
                ProgressStatus.PASSED
                if validation.conforms
                else ProgressStatus.FAILED
            ),
            message=(
                "Rule SHACL validation passed"
                if validation.conforms
                else "Rule SHACL violations remain"
            ),
            iteration=iteration,
            max_iterations=MAX_REVISION_ITERATIONS,
        )
        final_output = output
        rule_history.append(
            {
                "iteration": iteration,
                "phase": "shacl_revision",
                "output": _output_payload(output),
                "validation": _validation_payload(validation),
            }
        )

    def assert_fixed_resources() -> None:
        _assert_fixed_resources(
            rule_ontology,
            rule_shapes,
            rule_ontology_hash,
            rule_shapes_hash,
            "Rule",
        )

    def review_rdf(current_rdf: str, review_iteration: int) -> SelfReviewResult:
        review_output = run_data_rule_agent(
            DataRuleAgentMode.RULE_SELF_REVIEW,
            {
                "scenario_rdf_turtle": scenario_turtle,
                "source_document": source_document,
                "validated_data_rdf": data_turtle,
                "ontology_turtle": rule_ontology,
                "rule_shacl_turtle": rule_shapes,
                "current_rule_rdf": current_rdf,
                "self_review_iteration": review_iteration,
                "ontology_hash": rule_ontology_hash,
                "rule_shapes_hash": rule_shapes_hash,
            },
            model=model,
            runner=runner,
        )
        return _require_self_review(review_output)

    def revise_from_self_review(
        current_rdf: str,
        self_review_result: SelfReviewResult,
        current_validation: WorkflowRdfValidationResult,
        iteration: int,
    ) -> tuple[str, dict[str, Any], DataRuleAgentOutput]:
        revision_output = run_data_rule_agent(
            DataRuleAgentMode.RULE_REVISION,
            {
                "scenario_rdf_turtle": scenario_turtle,
                "source_document": source_document,
                "validated_data_rdf": data_turtle,
                "ontology_turtle": rule_ontology,
                "rule_shacl_turtle": rule_shapes,
                "previous_rule_rdf": current_rdf,
                "self_review_result": self_review_result.model_dump(mode="json"),
                "validation_feedback": validation_feedback(current_validation),
                "previous_validation": _validation_payload(current_validation),
                "revision_history": rule_history,
                "ontology_hash": rule_ontology_hash,
                "rule_shapes_hash": rule_shapes_hash,
            },
            model=model,
            runner=runner,
        )
        revised_rdf = _require_text(revision_output.rule_rdf_turtle, "rule_rdf_turtle")
        return revised_rdf, _output_payload(revision_output), revision_output

    def validate_revised_rdf(
        revised_rdf: str,
        iteration: int,
    ) -> WorkflowRdfValidationResult:
        return validate_rdf(
            revised_rdf,
            rule_ontology,
            rule_shapes,
            rdf_kind=RdfKind.RULE,
            additional_data_turtle=data_turtle,
            iteration=iteration,
        )

    rule_turtle, validation, self_review_history, revision_output = (
        run_self_review_loop(
            rdf_turtle=rule_turtle,
            validation=validation,
            rdf_kind=RdfKind.RULE,
            reviewer_agent=AgentName.RULE,
            revision_history=rule_history,
            review_rdf=review_rdf,
            revise_rdf=revise_from_self_review,
            validate_rdf=validate_revised_rdf,
            serialize_validation=_validation_payload,
            assert_fixed_resources=assert_fixed_resources,
            progress=progress,
            phase="rule",
        )
    )
    if revision_output is not None:
        final_output = revision_output
    return (
        initial_rule_turtle,
        rule_turtle,
        final_output,
        validation,
        rule_history,
        rule_shapes,
        ontology_validation,
        rule_shapes_hash,
        self_review_history,
    )


def run_data_rule_pipeline(
    scenario_file: Path | str,
    model: str,
    pdf_file: Path | str,
    output_dir: Path | str = "outputs/data_rule",
    runner: Callable[..., Any] | None = None,
    data_ontology_file: Path | str = DEFAULT_DATA_ONTOLOGY,
    rule_ontology_file: Path | str = DEFAULT_RULE_ONTOLOGY,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Generate Data/Rule RDF and one immutable SHACL graph for each RDF."""

    scenario_path = Path(scenario_file)
    data_rule_dir = Path(output_dir)
    data_rule_dir.mkdir(parents=True, exist_ok=True)
    if progress is not None:
        progress.phase(3, 6, "Data RDF", "data")
    (data_rule_dir / "data_shapes_generated.ttl").unlink(missing_ok=True)
    (data_rule_dir / "rule_shapes_generated.ttl").unlink(missing_ok=True)
    scenario_turtle = load_scenario_rdf(scenario_path)
    pdf_path = Path(pdf_file)
    with progress_operation(
        progress,
        phase="data",
        step="pdf_text_extraction",
        message="Data/Rule source document loading",
        completed_message="Data/Rule source document loaded",
    ):
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
        data_self_review,
    ) = _generate_and_revise_data(
        scenario_turtle,
        source_document_payload,
        model=model,
        runner=runner,
        data_ontology=data_ontology,
        data_ontology_hash=data_ontology_hash,
        output_dir=data_rule_dir,
        progress=progress,
    )
    write_text(data_rule_dir / "data_final.ttl", data_turtle)
    write_json(data_rule_dir / "data_validation.json", _validation_payload(data_validation))
    write_json(data_rule_dir / "data_revision_history.json", data_history)
    write_json(
        data_rule_dir / "data_self_review.json",
        data_self_review.model_dump(mode="json"),
    )
    report_progress(
        progress,
        phase="data",
        step="phase",
        status=(
            ProgressStatus.COMPLETED
            if data_self_review.status is SelfReviewRunStatus.PASSED
            else ProgressStatus.WARNING
        ),
        message="Data completed",
    )
    if progress is not None:
        progress.phase(4, 6, "Rule RDF", "rule")

    (
        initial_rule_turtle,
        rule_turtle,
        rule_output,
        rule_validation,
        rule_history,
        rule_shapes,
        rule_ontology_validation,
        rule_shapes_hash,
        rule_self_review,
    ) = _generate_and_revise_rule(
        scenario_turtle,
        source_document_payload,
        model=model,
        runner=runner,
        rule_ontology=rule_ontology,
        data_turtle=data_turtle,
        rule_ontology_hash=rule_ontology_hash,
        output_dir=data_rule_dir,
        progress=progress,
    )
    write_text(data_rule_dir / "rule_final.ttl", rule_turtle)
    write_json(data_rule_dir / "rule_validation.json", _validation_payload(rule_validation))
    write_json(data_rule_dir / "rule_revision_history.json", rule_history)
    write_json(
        data_rule_dir / "rule_self_review.json",
        rule_self_review.model_dump(mode="json"),
    )
    report_progress(
        progress,
        phase="rule",
        step="phase",
        status=(
            ProgressStatus.COMPLETED
            if rule_self_review.status is SelfReviewRunStatus.PASSED
            else ProgressStatus.WARNING
        ),
        message="Rule completed",
    )

    final_status = (
        "completed"
        if data_validation.conforms
        and rule_validation.conforms
        and data_ontology_validation.conforms
        and rule_ontology_validation.conforms
        and data_self_review.status is SelfReviewRunStatus.PASSED
        and rule_self_review.status is SelfReviewRunStatus.PASSED
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
        "max_data_iterations": MAX_REVISION_ITERATIONS,
        "max_rule_iterations": MAX_REVISION_ITERATIONS,
        "max_data_self_review_iterations": MAX_REVISION_ITERATIONS,
        "max_rule_self_review_iterations": MAX_REVISION_ITERATIONS,
        "data_self_review_status": data_self_review.status.value,
        "rule_self_review_status": rule_self_review.status.value,
        "final_status": final_status,
    }
    return {
        "output_dir": str(data_rule_dir),
        "final_status": final_status,
        "data_validation": _validation_payload(data_validation),
        "rule_validation": _validation_payload(rule_validation),
        "data_self_review": data_self_review.model_dump(mode="json"),
        "rule_self_review": rule_self_review.model_dump(mode="json"),
        "metadata": metadata,
    }
