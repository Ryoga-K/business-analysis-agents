"""Shared bounded loop for semantic RDF self-review and revision."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from business_analysis_agents.config import MAX_REVISION_ITERATIONS
from business_analysis_agents.models import (
    AgentName,
    RdfKind,
    SelfReviewHistory,
    SelfReviewIteration,
    SelfReviewResult,
    SelfReviewRunStatus,
    WorkflowRdfValidationResult,
)
from business_analysis_agents.rdf_validation import content_hash
from business_analysis_agents.progress import (
    ProgressReporter,
    ProgressStatus,
    progress_operation,
    report_progress,
)


ReviewRdf = Callable[[str, int], SelfReviewResult]
ReviseRdf = Callable[
    [str, SelfReviewResult, WorkflowRdfValidationResult, int],
    tuple[str, dict[str, Any], Any],
]
ValidateRdf = Callable[[str, int], WorkflowRdfValidationResult]
SerializeValidation = Callable[[WorkflowRdfValidationResult], dict[str, Any]]


def validate_self_review_result(
    result: SelfReviewResult,
    expected_agent: AgentName,
    expected_rdf_kind: RdfKind,
) -> None:
    """Reject inconsistent or wrong-target structured Self-Review output."""

    if result.reviewer_agent is not expected_agent:
        raise ValueError(
            "Self-Review returned an unexpected reviewer_agent: "
            f"expected={expected_agent.value}, actual={result.reviewer_agent.value}"
        )
    if result.rdf_kind is not expected_rdf_kind:
        raise ValueError(
            "Self-Review returned an unexpected rdf_kind: "
            f"expected={expected_rdf_kind.value}, actual={result.rdf_kind.value}"
        )
    if result.passed == bool(result.findings):
        raise ValueError(
            "Self-Review passed must be true exactly when findings is empty."
        )


def run_self_review_loop(
    rdf_turtle: str,
    validation: WorkflowRdfValidationResult,
    rdf_kind: RdfKind,
    reviewer_agent: AgentName,
    revision_history: list[dict[str, Any]],
    review_rdf: ReviewRdf,
    revise_rdf: ReviseRdf,
    validate_rdf: ValidateRdf,
    serialize_validation: SerializeValidation,
    assert_fixed_resources: Callable[[], None],
    progress: ProgressReporter | None = None,
    phase: str | None = None,
) -> tuple[
    str,
    WorkflowRdfValidationResult,
    SelfReviewHistory,
    Any | None,
]:
    """Run semantic review until passed, SHACL failure, or the retry limit."""

    iterations: list[SelfReviewIteration] = []
    last_result: SelfReviewResult | None = None
    last_revision_output: Any | None = None
    revision_count = 0
    progress_phase = phase or rdf_kind.value

    if not validation.conforms:
        report_progress(
            progress,
            phase=progress_phase,
            step="self_review",
            status=ProgressStatus.WARNING,
            message=f"{rdf_kind.value.title()} Self-Review skipped because SHACL failed",
        )
        return (
            rdf_turtle,
            validation,
            SelfReviewHistory(
                rdf_kind=rdf_kind,
                status=SelfReviewRunStatus.SHACL_FAILED,
                max_revision_iterations=MAX_REVISION_ITERATIONS,
            ),
            None,
        )

    review_iteration = 0
    while validation.conforms:
        assert_fixed_resources()
        with progress_operation(
            progress,
            phase=progress_phase,
            step="self_review",
            message=f"{rdf_kind.value.title()} Self-Review",
            completed_message=f"{rdf_kind.value.title()} Self-Review response received",
        ):
            result = review_rdf(rdf_turtle, review_iteration)
        validate_self_review_result(result, reviewer_agent, rdf_kind)
        last_result = result
        iteration_entry = SelfReviewIteration(
            iteration=review_iteration,
            rdf_hash=content_hash(rdf_turtle),
            result=result,
        )
        iterations.append(iteration_entry)
        if result.passed:
            report_progress(
                progress,
                phase=progress_phase,
                step="self_review",
                status=ProgressStatus.PASSED,
                message=f"{rdf_kind.value.title()} Self-Review passed",
            )
            return (
                rdf_turtle,
                validation,
                SelfReviewHistory(
                    rdf_kind=rdf_kind,
                    status=SelfReviewRunStatus.PASSED,
                    max_revision_iterations=MAX_REVISION_ITERATIONS,
                    iterations=iterations,
                    final_result=result,
                ),
                last_revision_output,
            )
        report_progress(
            progress,
            phase=progress_phase,
            step="self_review",
            status=ProgressStatus.FAILED,
            message=(
                f"{rdf_kind.value.title()} Self-Review found "
                f"{len(result.findings)} issue(s)"
            ),
        )
        if revision_count >= MAX_REVISION_ITERATIONS:
            report_progress(
                progress,
                phase=progress_phase,
                step="self_review_revision",
                status=ProgressStatus.WARNING,
                message="Maximum Self-Review revision iterations reached",
                iteration=revision_count,
                max_iterations=MAX_REVISION_ITERATIONS,
            )
            break

        while revision_count < MAX_REVISION_ITERATIONS:
            assert_fixed_resources()
            next_iteration = len(revision_history) + 1
            with progress_operation(
                progress,
                phase=progress_phase,
                step="self_review_revision",
                message=f"{rdf_kind.value.title()} Self-Review revision",
                completed_message=(
                    f"{rdf_kind.value.title()} Self-Review revision completed"
                ),
                iteration=revision_count + 1,
                max_iterations=MAX_REVISION_ITERATIONS,
                start_status=ProgressStatus.REVISION,
            ):
                rdf_turtle, output_payload, revision_output = revise_rdf(
                    rdf_turtle,
                    result,
                    validation,
                    next_iteration,
                )
            revision_count += 1
            last_revision_output = revision_output
            validation = validate_rdf(rdf_turtle, next_iteration)
            report_progress(
                progress,
                phase=progress_phase,
                step="shacl_validation",
                status=(
                    ProgressStatus.PASSED
                    if validation.conforms
                    else ProgressStatus.FAILED
                ),
                message=(
                    f"{rdf_kind.value.title()} SHACL validation passed"
                    if validation.conforms
                    else f"{rdf_kind.value.title()} SHACL violations remain"
                ),
                iteration=revision_count,
                max_iterations=MAX_REVISION_ITERATIONS,
            )
            revision_history.append(
                {
                    "iteration": next_iteration,
                    "phase": "self_review_revision",
                    "self_review_iteration": review_iteration,
                    "self_review_result": result.model_dump(mode="json"),
                    "output": output_payload,
                    "validation": serialize_validation(validation),
                }
            )
            iteration_entry.revision_performed = True
            iteration_entry.post_revision_validation_conforms = validation.conforms
            if validation.conforms:
                break

        if not validation.conforms:
            return (
                rdf_turtle,
                validation,
                SelfReviewHistory(
                    rdf_kind=rdf_kind,
                    status=SelfReviewRunStatus.SHACL_FAILED,
                    max_revision_iterations=MAX_REVISION_ITERATIONS,
                    iterations=iterations,
                    final_result=last_result,
                ),
                last_revision_output,
            )
        review_iteration += 1

    return (
        rdf_turtle,
        validation,
        SelfReviewHistory(
            rdf_kind=rdf_kind,
            status=SelfReviewRunStatus.MAX_ITERATIONS,
            max_revision_iterations=MAX_REVISION_ITERATIONS,
            iterations=iterations,
            final_result=last_result,
        ),
        last_revision_output,
    )
