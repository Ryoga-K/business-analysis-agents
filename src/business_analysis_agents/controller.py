"""CLIから呼び出す最上位の制御処理。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from business_analysis_agents.agents.scenario import (
    MissingOpenAIAPIKeyError as ScenarioMissingOpenAIAPIKeyError,
    run_scenario_agent,
    save_scenario_output,
)
from business_analysis_agents.agents.data_rule import (
    MissingOpenAIAPIKeyError as DataRuleMissingOpenAIAPIKeyError,
)
from business_analysis_agents.agents.consistency import (
    MissingOpenAIAPIKeyError as ConsistencyMissingOpenAIAPIKeyError,
)
from business_analysis_agents.agents.workflow import (
    MissingOpenAIAPIKeyError as WorkflowMissingOpenAIAPIKeyError,
)
from business_analysis_agents.config import (
    MAX_REVISION_ITERATIONS,
    load_config_from_env,
)
from business_analysis_agents.consistency_pipeline import (
    DEFAULT_DATA_RDF,
    DEFAULT_DATA_VALIDATION,
    DEFAULT_RULE_RDF,
    DEFAULT_RULE_VALIDATION,
    DEFAULT_WORKFLOW_RDF,
    DEFAULT_WORKFLOW_VALIDATION,
    run_consistency_pipeline,
)
from business_analysis_agents.data_rule_pipeline import run_data_rule_pipeline
from business_analysis_agents.fixed_resources import (
    DEFAULT_DATA_ONTOLOGY,
    DEFAULT_RULE_ONTOLOGY,
    DEFAULT_SCENARIO_ONTOLOGY,
    DEFAULT_WORKFLOW_ONTOLOGY,
    load_fixed_turtle,
)
from business_analysis_agents.document_loader import load_pdf_document
from business_analysis_agents.models import (
    AgentName,
    ControllerRunSummary,
    ControllerStage,
    ControllerStageResult,
    ControllerStageStatus,
    FinalArtifactSummary,
    FinalizationStatus,
    HumanReviewDecision,
    HumanReviewReport,
    IndividualRdfIssueSummary,
    RdfKind,
    ReviewStatus,
    RunStatus,
    ScenarioAgentInput,
)
from business_analysis_agents.rdf_validation import content_hash, parse_turtle
from business_analysis_agents.progress import (
    ProgressReporter,
    ProgressStatus,
    progress_operation,
    report_progress,
)
from business_analysis_agents.review.cli import (
    DEFAULT_CONSISTENCY_EVALUATION as DEFAULT_REVIEW_CONSISTENCY_EVALUATION,
    DEFAULT_DATA_RDF as DEFAULT_REVIEW_DATA_RDF,
    DEFAULT_HUMAN_REVIEW_OUTPUT,
    DEFAULT_RULE_RDF as DEFAULT_REVIEW_RULE_RDF,
    DEFAULT_WORKFLOW_RDF as DEFAULT_REVIEW_WORKFLOW_RDF,
    HumanReviewInputError,
    run_human_review,
)
from business_analysis_agents.workflow_pipeline import (
    run_workflow_pipeline,
    write_json,
    write_text,
)
from business_analysis_agents.targeted_revision_pipeline import (
    run_targeted_rdf_revision,
)

def run_scenario_pipeline(
    pdf_file: Path | str,
    model: str,
    output_dir: Path | str = "outputs/scenario",
    ontology_file: Path | str = DEFAULT_SCENARIO_ONTOLOGY,
    progress: ProgressReporter | None = None,
) -> dict[str, str]:
    """Run the existing Scenario generation flow and save Scenario RDF."""

    pdf_path = Path(pdf_file)
    with progress_operation(
        progress,
        phase="scenario",
        step="pdf_text_extraction",
        message="PDF text extraction",
        completed_message="PDF text extracted",
    ):
        document = load_pdf_document(pdf_path)
    ontology_path, scenario_ontology = load_fixed_turtle(
        ontology_file,
        "Scenario ontology",
    )
    with progress_operation(
        progress,
        phase="scenario",
        step="rdf_generation",
        message="Scenario RDF generation",
        completed_message="Scenario RDF generated",
    ):
        output = run_scenario_agent(
            ScenarioAgentInput(
                document=document,
                ontology_turtle=scenario_ontology,
            ),
            model=model,
        )
    scenario_path = save_scenario_output(output, output_dir)
    return {
        "output_dir": str(Path(output_dir)),
        "final_status": "completed",
        "scenario_file": str(scenario_path),
        "ontology_file": str(ontology_path),
    }


def _stage_result(
    summary: ControllerRunSummary,
    stage: ControllerStage,
) -> ControllerStageResult:
    return next(result for result in summary.stages if result.stage is stage)


def _save_controller_summary(
    summary: ControllerRunSummary,
    summary_path: Path,
) -> None:
    write_json(summary_path, summary.model_dump(mode="json"))


def _finish_failed_run(
    summary: ControllerRunSummary,
    summary_path: Path,
    stage: ControllerStage,
    error_type: str,
    error_message: str,
    stage_status: ControllerStageStatus = ControllerStageStatus.FATAL_FAILED,
    progress: ProgressReporter | None = None,
) -> ControllerRunSummary:
    result = _stage_result(summary, stage)
    result.status = stage_status
    result.error_type = error_type
    result.error_message = error_message
    for later_result in summary.stages:
        if later_result.status is ControllerStageStatus.PENDING:
            later_result.status = ControllerStageStatus.SKIPPED
    summary.status = RunStatus.FATAL_FAILED
    summary.completed = False
    summary.final_status = FinalizationStatus.PIPELINE_FAILED
    summary.fatal_error = True
    summary.failed_stage = stage
    summary.error_message = error_message
    summary.finished_at = datetime.now().astimezone()
    _save_controller_summary(summary, summary_path)
    report_progress(
        progress,
        phase=stage.value,
        step="phase",
        status=(
            ProgressStatus.WARNING
            if stage_status is ControllerStageStatus.COMPLETED_WITH_ISSUES
            else ProgressStatus.FAILED
        ),
        message=(
            f"{stage.value.replace('_', ' ').title()} ended with "
            f"{error_type}"
        ),
    )
    return summary


def _group_consistency_repairs(
    evaluation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Group violation analyses by target RDF for one revision call per target."""

    analyses = evaluation.get("violation_analyses")
    violations = evaluation.get("violations")
    if not isinstance(analyses, list) or not isinstance(violations, list):
        raise ValueError("Consistency evaluation has invalid findings data.")
    grouped: dict[AgentName, dict[str, Any]] = {}
    allowed = (AgentName.WORKFLOW, AgentName.DATA, AgentName.RULE)
    for analysis in analyses:
        if not isinstance(analysis, dict):
            raise ValueError("Consistency violation analysis must be an object.")
        try:
            target = AgentName(str(analysis["target_agent"]))
            violation_index = int(analysis["violation_index"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Consistency violation analysis is incomplete.") from error
        if target not in allowed:
            raise ValueError(f"Unsupported consistency repair target: {target.value}")
        if violation_index < 0 or violation_index >= len(violations):
            raise ValueError(
                f"Consistency violation index is out of range: {violation_index}"
            )
        bundle = grouped.setdefault(
            target,
            {
                "target_agent": target.value,
                "violation_indices": [],
                "repair_instructions": [],
                "items": [],
            },
        )
        instruction = str(analysis.get("repair_instruction", "")).strip()
        if not instruction:
            raise ValueError("Consistency repair instruction is empty.")
        bundle["violation_indices"].append(violation_index)
        instructions = bundle["repair_instructions"]
        if instruction not in instructions:
            instructions.append(instruction)
        bundle["items"].append(
            {
                "violation": violations[violation_index],
                "analysis": analysis,
            }
        )
    return [grouped[target] for target in allowed if target in grouped]


def _group_human_review_repairs(
    human_review: HumanReviewReport,
) -> list[dict[str, Any]]:
    """Collect revision-approved Human Review findings once per target RDF."""

    revision_decisions = {
        HumanReviewDecision.APPROVE_FINDING,
        HumanReviewDecision.PROVIDE_CONTEXT,
    }
    grouped: dict[AgentName, dict[str, Any]] = {}
    allowed = (AgentName.WORKFLOW, AgentName.DATA, AgentName.RULE)
    for finding in human_review.findings:
        if finding.decision not in revision_decisions:
            continue
        target = finding.source_analysis.target_agent
        if target not in allowed:
            raise ValueError(
                f"Unsupported Human Review revision target: {target.value}"
            )
        instruction = finding.source_analysis.repair_instruction
        if finding.supplemental_comment:
            instruction = (
                f"{instruction}\nHuman Review supplemental context: "
                f"{finding.supplemental_comment}"
            )
        bundle = grouped.setdefault(
            target,
            {
                "source": "human_review",
                "target_agent": target.value,
                "violation_indices": [],
                "target_resources": [],
                "repair_instructions": [],
                "items": [],
            },
        )
        bundle["violation_indices"].append(
            finding.consistency_violation_index
        )
        if finding.target_resource not in bundle["target_resources"]:
            bundle["target_resources"].append(finding.target_resource)
        if instruction not in bundle["repair_instructions"]:
            bundle["repair_instructions"].append(instruction)
        bundle["items"].append(
            {
                "finding_id": finding.finding_id,
                "target_resource": finding.target_resource,
                "human_decision": finding.decision.value,
                "human_response": finding.supplemental_comment,
                "violation": finding.source_violation.model_dump(mode="json"),
                "analysis": finding.source_analysis.model_dump(mode="json"),
                "revision_instruction": instruction,
            }
        )
    return [grouped[target] for target in allowed if target in grouped]


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Required {label} was not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid {label} JSON: {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _history_length(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid revision history JSON: {path}: {error}") from error
    if not isinstance(payload, list):
        raise ValueError(f"Revision history must be a JSON array: {path}")
    return len(payload)


def _require_parseable_turtle(path: Path | str, label: str) -> str:
    turtle_path = Path(path)
    if not turtle_path.is_file():
        raise FileNotFoundError(f"Required {label} was not found: {turtle_path}")
    turtle = turtle_path.read_text(encoding="utf-8")
    if not turtle.strip():
        raise ValueError(f"Required {label} is empty: {turtle_path}")
    graph, errors = parse_turtle(turtle)
    if graph is None:
        details = "; ".join(error.error_message for error in errors)
        raise ValueError(f"Required {label} is not parseable Turtle: {details}")
    return turtle


def _collect_rdf_issue_summary(
    *,
    rdf_kind: RdfKind,
    rdf_file: Path,
    shapes_file: Path,
    validation_file: Path,
    self_review_file: Path,
    revision_history_file: Path,
) -> IndividualRdfIssueSummary:
    """Require downstream artifacts and retain unresolved quality findings."""

    _require_parseable_turtle(rdf_file, f"{rdf_kind.value.title()} RDF")
    _require_parseable_turtle(
        shapes_file,
        f"generated {rdf_kind.value.title()} SHACL",
    )
    validation = _load_json_object(
        validation_file,
        f"{rdf_kind.value.title()} validation",
    )
    self_review = _load_json_object(
        self_review_file,
        f"{rdf_kind.value.title()} Self-Review",
    )
    _history_length(revision_history_file)

    conforms = validation.get("conforms")
    if not isinstance(conforms, bool):
        raise ValueError(
            f"{rdf_kind.value.title()} validation must contain boolean conforms: "
            f"{validation_file}"
        )
    shacl_conforms = validation.get("shacl_conforms", conforms)
    if not isinstance(shacl_conforms, bool):
        raise ValueError(
            f"{rdf_kind.value.title()} validation must contain boolean "
            f"shacl_conforms: {validation_file}"
        )
    shacl_result = validation.get("shacl_result")
    shacl_violations = (
        shacl_result.get("violations", [])
        if isinstance(shacl_result, dict)
        else []
    )
    if not isinstance(shacl_violations, list):
        raise ValueError(f"Invalid SHACL violations in {validation_file}")

    vocabulary = validation.get("vocabulary")
    vocabulary = vocabulary if isinstance(vocabulary, dict) else {}
    unauthorized_terms = vocabulary.get("unauthorized_terms", [])
    if not isinstance(unauthorized_terms, list):
        raise ValueError(f"Invalid unauthorized terms in {validation_file}")

    self_review_status = self_review.get("status")
    if not isinstance(self_review_status, str) or not self_review_status:
        raise ValueError(
            f"{rdf_kind.value.title()} Self-Review must contain status: "
            f"{self_review_file}"
        )
    final_result = self_review.get("final_result")
    self_review_findings = (
        final_result.get("findings", [])
        if isinstance(final_result, dict)
        else []
    )
    if not isinstance(self_review_findings, list):
        raise ValueError(f"Invalid Self-Review findings in {self_review_file}")

    return IndividualRdfIssueSummary(
        rdf_kind=rdf_kind,
        rdf_file=str(rdf_file),
        usable=True,
        validation_conforms=conforms,
        shacl_conforms=shacl_conforms,
        shacl_violation_count=len(shacl_violations),
        shacl_violations=shacl_violations,
        vocabulary_violation_count=len(unauthorized_terms),
        unauthorized_terms=[str(term) for term in unauthorized_terms],
        self_review_status=self_review_status,
        self_review_finding_count=len(self_review_findings),
        self_review_findings=self_review_findings,
    )


def _collect_all_rdf_issues(
    workflow_dir: Path,
    data_rule_dir: Path,
) -> dict[str, IndividualRdfIssueSummary]:
    locations = {
        RdfKind.WORKFLOW: workflow_dir,
        RdfKind.DATA: data_rule_dir,
        RdfKind.RULE: data_rule_dir,
    }
    return {
        rdf_kind.value: _collect_rdf_issue_summary(
            rdf_kind=rdf_kind,
            rdf_file=directory / f"{rdf_kind.value}_final.ttl",
            shapes_file=directory / f"{rdf_kind.value}_shapes_generated.ttl",
            validation_file=directory / f"{rdf_kind.value}_validation.json",
            self_review_file=directory / f"{rdf_kind.value}_self_review.json",
            revision_history_file=(
                directory / f"{rdf_kind.value}_revision_history.json"
            ),
        )
        for rdf_kind, directory in locations.items()
    }


def _report_rdf_issues(
    issue: IndividualRdfIssueSummary,
    progress: ProgressReporter | None,
) -> None:
    if not issue.has_issues:
        return
    name = issue.rdf_kind.value.title()
    if issue.shacl_violation_count or not issue.validation_conforms:
        report_progress(
            progress,
            phase=issue.rdf_kind.value,
            step="shacl_validation",
            status=ProgressStatus.WARNING,
            message=(
                f"{name} validation ended with "
                f"{issue.shacl_violation_count} unresolved SHACL violation(s)"
            ),
        )
    if issue.self_review_status != "passed":
        report_progress(
            progress,
            phase=issue.rdf_kind.value,
            step="self_review",
            status=ProgressStatus.WARNING,
            message=(
                f"{name} Self-Review ended with "
                f"{issue.self_review_finding_count} unresolved finding(s)"
            ),
        )
    report_progress(
        progress,
        phase=issue.rdf_kind.value,
        step="phase",
        status=ProgressStatus.WARNING,
        message=f"{name} completed with issues",
    )
    report_progress(
        progress,
        phase=issue.rdf_kind.value,
        step="continue",
        status=ProgressStatus.RUNNING,
        message="Continuing to next phase",
    )


def _sync_individual_stage_states(
    summary: ControllerRunSummary,
) -> None:
    workflow_issue = summary.individual_rdf_issues.get(RdfKind.WORKFLOW.value)
    workflow_stage = _stage_result(summary, ControllerStage.WORKFLOW)
    if workflow_issue is not None:
        workflow_stage.unresolved_shacl_violation_count = (
            workflow_issue.shacl_violation_count
        )
        workflow_stage.unresolved_self_review_finding_count = (
            workflow_issue.self_review_finding_count
        )
        workflow_stage.status = (
            ControllerStageStatus.COMPLETED_WITH_ISSUES
            if workflow_issue.has_issues
            else ControllerStageStatus.COMPLETED
        )

    data_issue = summary.individual_rdf_issues.get(RdfKind.DATA.value)
    rule_issue = summary.individual_rdf_issues.get(RdfKind.RULE.value)
    data_rule_stage = _stage_result(summary, ControllerStage.DATA_RULE)
    if data_issue is not None and rule_issue is not None:
        data_rule_stage.unresolved_shacl_violation_count = sum(
            issue.shacl_violation_count for issue in (data_issue, rule_issue)
        )
        data_rule_stage.unresolved_self_review_finding_count = sum(
            issue.self_review_finding_count for issue in (data_issue, rule_issue)
        )
        data_rule_stage.status = (
            ControllerStageStatus.COMPLETED_WITH_ISSUES
            if data_issue.has_issues or rule_issue.has_issues
            else ControllerStageStatus.COMPLETED
        )


def run_human_review_revision(
    *,
    human_review: HumanReviewReport,
    consistency_result: dict[str, Any],
    consistency_iteration: int,
    model: str,
    pdf_file: Path | str,
    scenario_file: Path | str,
    workflow_dir: Path,
    data_rule_dir: Path,
    consistency_dir: Path,
    human_review_dir: Path,
    workflow_ontology_file: Path | str,
    data_ontology_file: Path | str,
    rule_ontology_file: Path | str,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Apply Human Review instructions once and re-run fixed validations."""

    bundles = _group_human_review_repairs(human_review)
    has_pending = any(
        finding.decision is HumanReviewDecision.PENDING
        for finding in human_review.findings
    )
    result: dict[str, Any] = {
        "revision_performed": bool(bundles),
        "pending_decisions": has_pending,
        "repair_bundles": bundles,
        "revision_results": [],
        "individual_checks_passed": True,
        "consistency_result": consistency_result,
    }
    history_file = human_review_dir / "human_review_revision.json"
    if not bundles:
        if human_review.status is ReviewStatus.APPROVED:
            result["final_status"] = (
                FinalizationStatus.COMPLETED_WITHOUT_HUMAN_REVISION.value
            )
            result["unresolved_reason"] = None
        else:
            result["final_status"] = (
                FinalizationStatus.UNRESOLVED_AFTER_HUMAN_REVIEW.value
            )
            result["unresolved_reason"] = (
                "Human Review contains unresolved decisions."
            )
        write_json(history_file, result)
        return result

    workflow_file = workflow_dir / "workflow_final.ttl"
    data_file = data_rule_dir / "data_final.ttl"
    rule_file = data_rule_dir / "rule_final.ttl"
    for bundle in bundles:
        target = AgentName(str(bundle["target_agent"]))
        with progress_operation(
            progress,
            phase="finalization",
            step="human_review_revision",
            message="Human Review revision",
            completed_message="Human Review RDF revision completed",
            target=target.value,
            start_status=ProgressStatus.REVISION,
        ):
            revision_result = run_targeted_rdf_revision(
                target_agent=target,
                repair_bundle=bundle,
                consistency_iteration=consistency_iteration,
                scenario_file=scenario_file,
                pdf_file=pdf_file,
                model=model,
                workflow_file=workflow_file,
                data_file=data_file,
                rule_file=rule_file,
                workflow_shapes_file=(
                    workflow_dir / "workflow_shapes_generated.ttl"
                ),
                data_shapes_file=data_rule_dir / "data_shapes_generated.ttl",
                rule_shapes_file=data_rule_dir / "rule_shapes_generated.ttl",
                workflow_ontology_file=workflow_ontology_file,
                data_ontology_file=data_ontology_file,
                rule_ontology_file=rule_ontology_file,
                workflow_validation_file=(
                    workflow_dir / "workflow_validation.json"
                ),
                data_validation_file=data_rule_dir / "data_validation.json",
                rule_validation_file=data_rule_dir / "rule_validation.json",
                workflow_revision_history_file=(
                    workflow_dir / "workflow_revision_history.json"
                ),
                data_revision_history_file=(
                    data_rule_dir / "data_revision_history.json"
                ),
                rule_revision_history_file=(
                    data_rule_dir / "rule_revision_history.json"
                ),
                workflow_self_review_file=(
                    workflow_dir / "workflow_self_review.json"
                ),
                data_self_review_file=data_rule_dir / "data_self_review.json",
                rule_self_review_file=data_rule_dir / "rule_self_review.json",
                progress=progress,
            )
        result["revision_results"].append(revision_result)
        if revision_result.get("final_status") != "completed":
            result["individual_checks_passed"] = False
            report_progress(
                progress,
                phase=target.value,
                step="human_review_revision",
                status=ProgressStatus.WARNING,
                message=(
                    f"{target.value.title()} Human Review revision retained "
                    "unresolved individual issues"
                ),
            )

    cross_shapes_hash = str(consistency_result.get("cross_shapes_hash", ""))
    if not cross_shapes_hash:
        raise ValueError("Consistency result did not include cross_shapes_hash.")
    final_consistency = run_consistency_pipeline(
        model=model,
        workflow_file=workflow_file,
        data_file=data_file,
        rule_file=rule_file,
        workflow_validation_file=workflow_dir / "workflow_validation.json",
        data_validation_file=data_rule_dir / "data_validation.json",
        rule_validation_file=data_rule_dir / "rule_validation.json",
        output_dir=consistency_dir,
        workflow_ontology_file=workflow_ontology_file,
        data_ontology_file=data_ontology_file,
        rule_ontology_file=rule_ontology_file,
        cross_shacl_file=consistency_dir / "consistency_shapes_generated.ttl",
        expected_cross_shapes_hash=cross_shapes_hash,
        progress=progress,
    )
    result["consistency_result"] = final_consistency
    if has_pending or final_consistency.get("final_status") != "completed":
        result["final_status"] = (
            FinalizationStatus.UNRESOLVED_AFTER_HUMAN_REVIEW.value
        )
        result["unresolved_reason"] = (
            "Human Review decisions remain pending or Cross Consistency still "
            "reports violations."
        )
    elif not result["individual_checks_passed"]:
        result["final_status"] = (
            FinalizationStatus.INDIVIDUAL_VALIDATION_FAILED.value
        )
        result["unresolved_reason"] = (
            "A Human Review targeted revision retained individual SHACL or "
            "Self-Review issues."
        )
    else:
        result["final_status"] = (
            FinalizationStatus.COMPLETED_AFTER_HUMAN_REVISION.value
        )
        result["unresolved_reason"] = None
    write_json(history_file, result)
    return result


def _save_final_artifacts(
    *,
    root: Path,
    scenario_file: Path | str,
    workflow_dir: Path,
    data_rule_dir: Path,
    consistency_result: dict[str, Any],
    consistency_revision_history: dict[str, Any],
    final_status: FinalizationStatus,
    e2e_status: RunStatus,
    individual_rdf_issues: dict[str, IndividualRdfIssueSummary],
    human_review_revision: dict[str, Any],
    unresolved_reason: str | None,
    progress: ProgressReporter | None,
) -> FinalArtifactSummary:
    """Snapshot the last accepted RDF files and their existing validation states."""

    final_dir = root / "final"
    source_files = {
        "scenario": Path(scenario_file),
        "workflow": workflow_dir / "workflow_final.ttl",
        "data": data_rule_dir / "data_final.ttl",
        "rule": data_rule_dir / "rule_final.ttl",
    }
    destination_files = {
        name: final_dir / f"{name}_final.ttl" for name in source_files
    }
    turtles: dict[str, str] = {}
    for name, source in source_files.items():
        if not source.is_file():
            raise FileNotFoundError(f"Required final {name} RDF was not found: {source}")
        turtle = source.read_text(encoding="utf-8")
        if not turtle.strip():
            raise ValueError(f"Required final {name} RDF is empty: {source}")
        turtles[name] = turtle
        write_text(destination_files[name], turtle)

    validations = {
        "workflow": _load_json_object(
            workflow_dir / "workflow_validation.json", "Workflow validation"
        ),
        "data": _load_json_object(
            data_rule_dir / "data_validation.json", "Data validation"
        ),
        "rule": _load_json_object(
            data_rule_dir / "rule_validation.json", "Rule validation"
        ),
    }
    self_reviews = {
        "workflow": _load_json_object(
            workflow_dir / "workflow_self_review.json", "Workflow Self-Review"
        ),
        "data": _load_json_object(
            data_rule_dir / "data_self_review.json", "Data Self-Review"
        ),
        "rule": _load_json_object(
            data_rule_dir / "rule_self_review.json", "Rule Self-Review"
        ),
    }
    revision_counts = {
        "workflow": _history_length(
            workflow_dir / "workflow_revision_history.json"
        ),
        "data": _history_length(data_rule_dir / "data_revision_history.json"),
        "rule": _history_length(data_rule_dir / "rule_revision_history.json"),
        "cross": int(consistency_revision_history.get("revision_rounds", 0)),
        "human_review_targets": len(
            human_review_revision.get("revision_results", [])
        ),
    }
    final_summary = FinalArtifactSummary(
        final_status=final_status,
        e2e_status=e2e_status,
        final_rdf_files={
            name: str(path) for name, path in destination_files.items()
        },
        shacl_conforms={
            name: payload.get("conforms") is True
            for name, payload in validations.items()
        },
        self_review_status={
            name: str(payload.get("status", "unknown"))
            for name, payload in self_reviews.items()
        },
        cross_consistency_status=str(
            consistency_result.get("final_status", "unknown")
        ),
        human_review_performed=True,
        human_review_revision_performed=bool(
            human_review_revision.get("revision_performed")
        ),
        revision_count=sum(
            revision_counts[name] for name in ("workflow", "data", "rule")
        ),
        revision_counts=revision_counts,
        final_rdf_hashes={
            name: content_hash(turtle) for name, turtle in turtles.items()
        },
        individual_rdf_issues=individual_rdf_issues,
        unresolved_reason=unresolved_reason,
    )
    write_json(
        final_dir / "final_summary.json",
        final_summary.model_dump(mode="json"),
    )
    report_progress(
        progress,
        phase="finalization",
        step="final_artifacts",
        status=ProgressStatus.COMPLETED,
        message="Final artifacts saved",
    )
    return final_summary


def _rdf_hashes(
    workflow_file: Path,
    data_file: Path,
    rule_file: Path,
) -> dict[str, str]:
    return {
        "workflow": content_hash(workflow_file.read_text(encoding="utf-8")),
        "data": content_hash(data_file.read_text(encoding="utf-8")),
        "rule": content_hash(rule_file.read_text(encoding="utf-8")),
    }


def run_consistency_revision_loop(
    *,
    model: str,
    pdf_file: Path | str,
    scenario_file: Path | str,
    workflow_dir: Path,
    data_rule_dir: Path,
    consistency_dir: Path,
    workflow_ontology_file: Path | str,
    data_ontology_file: Path | str,
    rule_ontology_file: Path | str,
    progress: ProgressReporter | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run Cross Consistency and bounded targeted revision rounds."""

    workflow_file = workflow_dir / "workflow_final.ttl"
    data_file = data_rule_dir / "data_final.ttl"
    rule_file = data_rule_dir / "rule_final.ttl"
    cross_shapes_file = consistency_dir / "consistency_shapes_generated.ttl"
    history_file = consistency_dir / "consistency_revision_history.json"

    consistency_result = run_consistency_pipeline(
        model=model,
        workflow_file=workflow_file,
        data_file=data_file,
        rule_file=rule_file,
        workflow_validation_file=workflow_dir / "workflow_validation.json",
        data_validation_file=data_rule_dir / "data_validation.json",
        rule_validation_file=data_rule_dir / "rule_validation.json",
        output_dir=consistency_dir,
        workflow_ontology_file=workflow_ontology_file,
        data_ontology_file=data_ontology_file,
        rule_ontology_file=rule_ontology_file,
        progress=progress,
    )
    cross_shapes_hash = str(consistency_result.get("cross_shapes_hash", ""))
    if not cross_shapes_hash:
        raise ValueError("Consistency result did not include cross_shapes_hash.")
    iteration_history: list[dict[str, Any]] = []
    history: dict[str, Any] = {
        "status": "running",
        "max_cross_revision_iterations": MAX_REVISION_ITERATIONS,
        "cross_shapes_hash": cross_shapes_hash,
        "iterations": iteration_history,
    }
    revision_round = 0
    consistency_iteration = 0

    while True:
        evaluation = consistency_result.get("evaluation")
        if not isinstance(evaluation, dict):
            raise ValueError("Consistency result did not include evaluation.")
        conforms = evaluation.get("conforms") is True
        bundles = _group_consistency_repairs(evaluation)
        record: dict[str, Any] = {
            "iteration": consistency_iteration,
            "cross_conforms": conforms,
            "cross_status": consistency_result.get("final_status"),
            "cross_shapes_hash": cross_shapes_hash,
            "rdf_hashes": _rdf_hashes(workflow_file, data_file, rule_file),
            "repair_bundles": bundles,
            "revision_results": [],
        }
        if conforms:
            report_progress(
                progress,
                phase="consistency",
                step="cross_validation",
                status=ProgressStatus.COMPLETED,
                message="Cross Consistency passed",
            )
            iteration_history.append(record)
            history["status"] = "completed"
            break
        if revision_round >= MAX_REVISION_ITERATIONS:
            report_progress(
                progress,
                phase="consistency",
                step="cross_revision",
                status=ProgressStatus.WARNING,
                message="Maximum Cross revision iterations reached",
                iteration=revision_round,
                max_iterations=MAX_REVISION_ITERATIONS,
            )
            iteration_history.append(record)
            history["status"] = "max_iterations"
            break
        if evaluation.get("can_auto_repair") is not True or not bundles:
            report_progress(
                progress,
                phase="consistency",
                step="violation_analysis",
                status=ProgressStatus.WARNING,
                message="Cross violations cannot be repaired automatically",
            )
            iteration_history.append(record)
            history["status"] = "not_auto_repairable"
            break

        revision_results: list[dict[str, Any]] = []
        revision_failed = False
        for bundle in bundles:
            target = AgentName(str(bundle["target_agent"]))
            with progress_operation(
                progress,
                phase="consistency",
                step="targeted_revision",
                message="Cross revision",
                completed_message="Targeted RDF revision completed",
                iteration=revision_round + 1,
                max_iterations=MAX_REVISION_ITERATIONS,
                target=target.value,
                start_status=ProgressStatus.REVISION,
            ):
                result = run_targeted_rdf_revision(
                    target_agent=target,
                    repair_bundle=bundle,
                    consistency_iteration=consistency_iteration,
                    scenario_file=scenario_file,
                    pdf_file=pdf_file,
                    model=model,
                    workflow_file=workflow_file,
                    data_file=data_file,
                    rule_file=rule_file,
                    workflow_shapes_file=(
                        workflow_dir / "workflow_shapes_generated.ttl"
                    ),
                    data_shapes_file=data_rule_dir / "data_shapes_generated.ttl",
                    rule_shapes_file=data_rule_dir / "rule_shapes_generated.ttl",
                    workflow_ontology_file=workflow_ontology_file,
                    data_ontology_file=data_ontology_file,
                    rule_ontology_file=rule_ontology_file,
                    workflow_validation_file=(
                        workflow_dir / "workflow_validation.json"
                    ),
                    data_validation_file=data_rule_dir / "data_validation.json",
                    rule_validation_file=data_rule_dir / "rule_validation.json",
                    workflow_revision_history_file=(
                        workflow_dir / "workflow_revision_history.json"
                    ),
                    data_revision_history_file=(
                        data_rule_dir / "data_revision_history.json"
                    ),
                    rule_revision_history_file=(
                        data_rule_dir / "rule_revision_history.json"
                    ),
                    workflow_self_review_file=(
                        workflow_dir / "workflow_self_review.json"
                    ),
                    data_self_review_file=data_rule_dir / "data_self_review.json",
                    rule_self_review_file=data_rule_dir / "rule_self_review.json",
                    progress=progress,
                )
            revision_results.append(result)
            if result.get("final_status") != "completed":
                revision_failed = True
        record["revision_results"] = revision_results
        record["rdf_hashes_after_revision"] = _rdf_hashes(
            workflow_file,
            data_file,
            rule_file,
        )
        iteration_history.append(record)
        write_json(history_file, history)
        if revision_failed:
            report_progress(
                progress,
                phase="consistency",
                step="targeted_revision",
                status=ProgressStatus.WARNING,
                message=(
                    "Targeted RDF did not pass individual checks; "
                    "continuing with the usable RDF"
                ),
            )

        revision_round += 1
        consistency_iteration += 1
        report_progress(
            progress,
            phase="consistency",
            step="cross_revalidation",
            status=ProgressStatus.RUNNING,
            message="Cross re-validation",
            iteration=revision_round,
            max_iterations=MAX_REVISION_ITERATIONS,
        )
        consistency_result = run_consistency_pipeline(
            model=model,
            workflow_file=workflow_file,
            data_file=data_file,
            rule_file=rule_file,
            workflow_validation_file=workflow_dir / "workflow_validation.json",
            data_validation_file=data_rule_dir / "data_validation.json",
            rule_validation_file=data_rule_dir / "rule_validation.json",
            output_dir=consistency_dir,
            workflow_ontology_file=workflow_ontology_file,
            data_ontology_file=data_ontology_file,
            rule_ontology_file=rule_ontology_file,
            cross_shacl_file=cross_shapes_file,
            expected_cross_shapes_hash=cross_shapes_hash,
            progress=progress,
        )

    history["revision_rounds"] = revision_round
    history["final_consistency_status"] = consistency_result.get("final_status")
    write_json(history_file, history)
    return consistency_result, history


def run_end_to_end_controller(
    pdf_file: Path | str,
    model: str,
    output_dir: Path | str = "outputs",
    scenario_ontology_file: Path | str = DEFAULT_SCENARIO_ONTOLOGY,
    workflow_ontology_file: Path | str = DEFAULT_WORKFLOW_ONTOLOGY,
    data_ontology_file: Path | str = DEFAULT_DATA_ONTOLOGY,
    rule_ontology_file: Path | str = DEFAULT_RULE_ONTOLOGY,
    reviewer: str | None = None,
    progress: ProgressReporter | None = None,
) -> ControllerRunSummary:
    """Run Scenario through Human Review revision and finalization in sequence."""

    root = Path(output_dir)
    scenario_dir = root / "scenario"
    workflow_dir = root / "workflow"
    data_rule_dir = root / "data_rule"
    consistency_dir = root / "consistency"
    summary_path = root / "controller" / "run_summary.json"
    progress_path = root / "controller" / "progress.jsonl"
    progress_reporter = progress or ProgressReporter(log_file=progress_path)
    stages = [
        ControllerStageResult(stage=stage)
        for stage in (
            ControllerStage.SCENARIO,
            ControllerStage.WORKFLOW,
            ControllerStage.DATA_RULE,
            ControllerStage.CONSISTENCY,
            ControllerStage.HUMAN_REVIEW,
        )
    ]
    summary = ControllerRunSummary(
        input_pdf=str(Path(pdf_file).resolve()),
        started_at=datetime.now().astimezone(),
        stages=stages,
        output_files={
            "run_summary": str(summary_path),
            "progress_log": str(progress_reporter.log_file or progress_path),
        },
    )
    _save_controller_summary(summary, summary_path)

    scenario_stage = _stage_result(summary, ControllerStage.SCENARIO)
    progress_reporter.phase(1, 6, "Scenario RDF", "scenario")
    try:
        scenario_result = run_scenario_pipeline(
            pdf_file=pdf_file,
            model=model,
            output_dir=scenario_dir,
            ontology_file=scenario_ontology_file,
            progress=progress_reporter,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.SCENARIO,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )
    scenario_file = scenario_result["scenario_file"]
    try:
        _require_parseable_turtle(scenario_file, "Scenario RDF")
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.SCENARIO,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )
    scenario_stage.status = ControllerStageStatus.COMPLETED
    scenario_stage.pipeline_status = scenario_result["final_status"]
    scenario_stage.output_files = {"scenario_rdf": scenario_file}
    summary.output_files.update(scenario_stage.output_files)
    report_progress(
        progress_reporter,
        phase="scenario",
        step="phase",
        status=ProgressStatus.COMPLETED,
        message="Scenario completed",
    )
    _save_controller_summary(summary, summary_path)

    workflow_stage = _stage_result(summary, ControllerStage.WORKFLOW)
    progress_reporter.phase(2, 6, "Workflow RDF", "workflow")
    try:
        workflow_result = run_workflow_pipeline(
            scenario_file=scenario_file,
            model=model,
            pdf_file=pdf_file,
            output_dir=workflow_dir,
            ontology_file=workflow_ontology_file,
            progress=progress_reporter,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.WORKFLOW,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )
    workflow_stage.pipeline_status = workflow_result["final_status"]
    workflow_stage.output_files = {
        "workflow_rdf": str(workflow_dir / "workflow_final.ttl"),
        "workflow_shapes": str(workflow_dir / "workflow_shapes_generated.ttl"),
        "workflow_validation": str(workflow_dir / "workflow_validation.json"),
        "workflow_revision_history": str(
            workflow_dir / "workflow_revision_history.json"
        ),
        "workflow_self_review": str(workflow_dir / "workflow_self_review.json"),
    }
    summary.output_files.update(workflow_stage.output_files)
    try:
        workflow_issue = _collect_rdf_issue_summary(
            rdf_kind=RdfKind.WORKFLOW,
            rdf_file=workflow_dir / "workflow_final.ttl",
            shapes_file=workflow_dir / "workflow_shapes_generated.ttl",
            validation_file=workflow_dir / "workflow_validation.json",
            self_review_file=workflow_dir / "workflow_self_review.json",
            revision_history_file=workflow_dir / "workflow_revision_history.json",
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.WORKFLOW,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )
    summary.individual_rdf_issues[RdfKind.WORKFLOW.value] = workflow_issue
    workflow_stage.unresolved_shacl_violation_count = (
        workflow_issue.shacl_violation_count
    )
    workflow_stage.unresolved_self_review_finding_count = (
        workflow_issue.self_review_finding_count
    )
    if workflow_issue.has_issues:
        workflow_stage.status = ControllerStageStatus.COMPLETED_WITH_ISSUES
        workflow_stage.warnings.append(
            "Workflow RDF retained unresolved individual validation issues."
        )
        _report_rdf_issues(workflow_issue, progress_reporter)
    else:
        workflow_stage.status = ControllerStageStatus.COMPLETED
        report_progress(
            progress_reporter,
            phase="workflow",
            step="phase",
            status=ProgressStatus.COMPLETED,
            message="Workflow completed",
        )
    _save_controller_summary(summary, summary_path)

    data_rule_stage = _stage_result(summary, ControllerStage.DATA_RULE)
    try:
        data_rule_result = run_data_rule_pipeline(
            scenario_file=scenario_file,
            model=model,
            pdf_file=pdf_file,
            output_dir=data_rule_dir,
            data_ontology_file=data_ontology_file,
            rule_ontology_file=rule_ontology_file,
            progress=progress_reporter,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.DATA_RULE,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )
    data_rule_stage.pipeline_status = data_rule_result["final_status"]
    data_rule_stage.output_files = {
        "data_rdf": str(data_rule_dir / "data_final.ttl"),
        "data_shapes": str(data_rule_dir / "data_shapes_generated.ttl"),
        "data_validation": str(data_rule_dir / "data_validation.json"),
        "data_revision_history": str(data_rule_dir / "data_revision_history.json"),
        "data_self_review": str(data_rule_dir / "data_self_review.json"),
        "rule_rdf": str(data_rule_dir / "rule_final.ttl"),
        "rule_shapes": str(data_rule_dir / "rule_shapes_generated.ttl"),
        "rule_validation": str(data_rule_dir / "rule_validation.json"),
        "rule_revision_history": str(data_rule_dir / "rule_revision_history.json"),
        "rule_self_review": str(data_rule_dir / "rule_self_review.json"),
    }
    summary.output_files.update(data_rule_stage.output_files)
    try:
        data_issue = _collect_rdf_issue_summary(
            rdf_kind=RdfKind.DATA,
            rdf_file=data_rule_dir / "data_final.ttl",
            shapes_file=data_rule_dir / "data_shapes_generated.ttl",
            validation_file=data_rule_dir / "data_validation.json",
            self_review_file=data_rule_dir / "data_self_review.json",
            revision_history_file=data_rule_dir / "data_revision_history.json",
        )
        rule_issue = _collect_rdf_issue_summary(
            rdf_kind=RdfKind.RULE,
            rdf_file=data_rule_dir / "rule_final.ttl",
            shapes_file=data_rule_dir / "rule_shapes_generated.ttl",
            validation_file=data_rule_dir / "rule_validation.json",
            self_review_file=data_rule_dir / "rule_self_review.json",
            revision_history_file=data_rule_dir / "rule_revision_history.json",
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.DATA_RULE,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )
    summary.individual_rdf_issues.update(
        {
            RdfKind.DATA.value: data_issue,
            RdfKind.RULE.value: rule_issue,
        }
    )
    data_rule_stage.unresolved_shacl_violation_count = sum(
        issue.shacl_violation_count for issue in (data_issue, rule_issue)
    )
    data_rule_stage.unresolved_self_review_finding_count = sum(
        issue.self_review_finding_count for issue in (data_issue, rule_issue)
    )
    if data_issue.has_issues or rule_issue.has_issues:
        data_rule_stage.status = ControllerStageStatus.COMPLETED_WITH_ISSUES
        data_rule_stage.warnings.append(
            "Data RDF or Rule RDF retained unresolved individual issues."
        )
        _report_rdf_issues(data_issue, progress_reporter)
        _report_rdf_issues(rule_issue, progress_reporter)
    else:
        data_rule_stage.status = ControllerStageStatus.COMPLETED
    _save_controller_summary(summary, summary_path)

    consistency_stage = _stage_result(summary, ControllerStage.CONSISTENCY)
    progress_reporter.phase(5, 6, "Cross Consistency", "consistency")
    try:
        consistency_result, consistency_history = run_consistency_revision_loop(
            model=model,
            pdf_file=pdf_file,
            scenario_file=scenario_file,
            workflow_dir=workflow_dir,
            data_rule_dir=data_rule_dir,
            consistency_dir=consistency_dir,
            workflow_ontology_file=workflow_ontology_file,
            data_ontology_file=data_ontology_file,
            rule_ontology_file=rule_ontology_file,
            progress=progress_reporter,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.CONSISTENCY,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )
    consistency_status = consistency_result["final_status"]
    consistency_stage.pipeline_status = consistency_status
    consistency_stage.output_files = {
        "consistency_shapes": str(
            consistency_dir / "consistency_shapes_generated.ttl"
        ),
        "consistency_validation": str(
            consistency_dir / "consistency_validation.json"
        ),
        "consistency_evaluation": str(
            consistency_dir / "consistency_evaluation.json"
        ),
        "consistency_revision_history": str(
            consistency_dir / "consistency_revision_history.json"
        ),
    }
    summary.output_files.update(consistency_stage.output_files)
    if consistency_status not in {"completed", "needs_revision"}:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.CONSISTENCY,
            "UnexpectedPipelineStatus",
            f"Unexpected Consistency pipeline status: {consistency_status}",
            progress=progress_reporter,
        )
    consistency_stage.status = (
        ControllerStageStatus.COMPLETED
        if consistency_status == "completed"
        else ControllerStageStatus.COMPLETED_WITH_ISSUES
    )
    if consistency_status != "completed":
        consistency_stage.warnings.append(
            f"Consistency revision loop ended: {consistency_history['status']}"
        )
        report_progress(
            progress_reporter,
            phase="consistency",
            step="phase",
            status=ProgressStatus.WARNING,
            message="Cross Consistency completed with unresolved findings",
        )
        report_progress(
            progress_reporter,
            phase="consistency",
            step="continue",
            status=ProgressStatus.RUNNING,
            message="Continuing to Human Review",
        )
    evaluation = consistency_result.get("evaluation")
    cross_violations = (
        evaluation.get("violations", []) if isinstance(evaluation, dict) else []
    )
    summary.cross_consistency_finding_count = (
        len(cross_violations) if isinstance(cross_violations, list) else 0
    )
    try:
        summary.individual_rdf_issues = _collect_all_rdf_issues(
            workflow_dir,
            data_rule_dir,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.CONSISTENCY,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )
    _sync_individual_stage_states(summary)
    summary.human_review_required = (
        consistency_status != "completed"
        or any(issue.has_issues for issue in summary.individual_rdf_issues.values())
    )
    _save_controller_summary(summary, summary_path)

    human_review_stage = _stage_result(summary, ControllerStage.HUMAN_REVIEW)
    progress_reporter.phase(6, 6, "Human Review / Finalization", "finalization")
    human_review_file = root / "human_review" / "human_review.json"
    report_progress(
        progress_reporter,
        phase="finalization",
        step="human_review",
        status=ProgressStatus.RUNNING,
        message="Human Review",
    )
    try:
        human_review = run_human_review(
            workflow_file=workflow_dir / "workflow_final.ttl",
            data_file=data_rule_dir / "data_final.ttl",
            rule_file=data_rule_dir / "rule_final.ttl",
            consistency_evaluation_file=(
                consistency_dir / "consistency_evaluation.json"
            ),
            output_file=human_review_file,
            reviewer=reviewer,
            individual_rdf_issues=summary.individual_rdf_issues,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.HUMAN_REVIEW,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )
    human_review_stage.status = ControllerStageStatus.COMPLETED
    human_review_stage.pipeline_status = human_review.status.value
    human_review_stage.output_files = {
        "human_review": str(human_review_file),
        "human_review_revision": str(
            root / "human_review" / "human_review_revision.json"
        ),
    }
    summary.output_files.update(human_review_stage.output_files)
    report_progress(
        progress_reporter,
        phase="finalization",
        step="human_review",
        status=ProgressStatus.PASSED,
        message="Human Review completed",
    )

    try:
        human_revision = run_human_review_revision(
            human_review=human_review,
            consistency_result=consistency_result,
            consistency_iteration=int(
                consistency_history.get("revision_rounds", 0)
            )
            + 1,
            model=model,
            pdf_file=pdf_file,
            scenario_file=scenario_file,
            workflow_dir=workflow_dir,
            data_rule_dir=data_rule_dir,
            consistency_dir=consistency_dir,
            human_review_dir=root / "human_review",
            workflow_ontology_file=workflow_ontology_file,
            data_ontology_file=data_ontology_file,
            rule_ontology_file=rule_ontology_file,
            progress=progress_reporter,
        )
        final_consistency = human_revision["consistency_result"]
        if not isinstance(final_consistency, dict):
            raise ValueError("Human Review revision returned invalid Consistency data.")

        final_cross_status = str(final_consistency.get("final_status", "unknown"))
        final_status = FinalizationStatus(str(human_revision["final_status"]))
        unresolved_reason = human_revision.get("unresolved_reason")

        consistency_stage.pipeline_status = final_cross_status
        consistency_stage.status = (
            ControllerStageStatus.COMPLETED
            if final_cross_status == "completed"
            else ControllerStageStatus.COMPLETED_WITH_ISSUES
        )
        if final_cross_status != "completed":
            consistency_stage.warnings.append(
                "Cross Consistency remains unresolved after Human Review."
            )
        final_evaluation = final_consistency.get("evaluation")
        final_cross_violations = (
            final_evaluation.get("violations", [])
            if isinstance(final_evaluation, dict)
            else []
        )
        summary.cross_consistency_finding_count = (
            len(final_cross_violations)
            if isinstance(final_cross_violations, list)
            else 0
        )
        summary.individual_rdf_issues = _collect_all_rdf_issues(
            workflow_dir,
            data_rule_dir,
        )
        _sync_individual_stage_states(summary)
        has_final_issues = (
            final_cross_status != "completed"
            or any(
                issue.has_issues
                for issue in summary.individual_rdf_issues.values()
            )
            or final_status
            in {
                FinalizationStatus.UNRESOLVED_AFTER_HUMAN_REVIEW,
                FinalizationStatus.INDIVIDUAL_VALIDATION_FAILED,
            }
        )
        e2e_status = (
            RunStatus.COMPLETED_WITH_ISSUES
            if has_final_issues
            else RunStatus.COMPLETED
        )
        final_summary = _save_final_artifacts(
            root=root,
            scenario_file=scenario_file,
            workflow_dir=workflow_dir,
            data_rule_dir=data_rule_dir,
            consistency_result=final_consistency,
            consistency_revision_history=consistency_history,
            final_status=final_status,
            e2e_status=e2e_status,
            individual_rdf_issues=summary.individual_rdf_issues,
            human_review_revision=human_revision,
            unresolved_reason=unresolved_reason,
            progress=progress_reporter,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.HUMAN_REVIEW,
            type(error).__name__,
            str(error),
            progress=progress_reporter,
        )

    summary.final_status = final_summary.final_status
    summary.output_files.update(
        {
            f"final_{name}_rdf": path
            for name, path in final_summary.final_rdf_files.items()
        }
    )
    summary.output_files["final_summary"] = str(
        root / "final" / "final_summary.json"
    )
    summary.status = final_summary.e2e_status
    summary.completed = True
    summary.fatal_error = False
    human_review_stage.status = (
        ControllerStageStatus.COMPLETED_WITH_ISSUES
        if summary.status is RunStatus.COMPLETED_WITH_ISSUES
        else ControllerStageStatus.COMPLETED
    )
    summary.finished_at = datetime.now().astimezone()
    _save_controller_summary(summary, summary_path)
    report_progress(
        progress_reporter,
        phase="finalization",
        step="human_review",
        status=ProgressStatus.COMPLETED,
        message="End-to-End execution completed",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    """CLI引数パーサーを作成する。"""

    parser = argparse.ArgumentParser(
        description="業務文書PDFから固定Ontology準拠のScenario RDFを生成します。"
    )
    parser.add_argument(
        "pdf_path",
        nargs="?",
        help="業務文書PDFのパス。未指定の場合は起動確認メッセージだけを表示します。",
    )
    return parser


def build_workflow_parser() -> argparse.ArgumentParser:
    """Create the workflow subcommand parser."""

    workflow_parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents workflow",
        description="Generate Workflow RDF from Scenario RDF and a PDF.",
    )
    workflow_parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario_final.ttl generated by the Scenario Agent.",
    )
    workflow_parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the original business document PDF.",
    )
    workflow_parser.add_argument(
        "--output-dir",
        default="outputs/workflow",
        help="Directory for Workflow RDF artifacts.",
    )
    workflow_parser.add_argument(
        "--ontology",
        default=str(DEFAULT_WORKFLOW_ONTOLOGY),
        help="Workflow ontology Turtle file.",
    )
    workflow_parser.add_argument(
        "--save-debug-outputs",
        action="store_true",
        help="Save ontology, SHACL, agent output, and run metadata artifacts.",
    )
    workflow_parser.add_argument(
        "--progress",
        action="store_true",
        help="Show detailed progress and save progress.jsonl in the output directory.",
    )
    return workflow_parser


def build_data_rule_parser() -> argparse.ArgumentParser:
    """Create the data-rule subcommand parser."""

    parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents data-rule",
        description="Generate Data RDF and Rule RDF from Scenario RDF and a PDF.",
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario_final.ttl generated by the Scenario Agent.",
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the original business document PDF.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/data_rule",
        help="Directory for Data RDF and Rule RDF artifacts.",
    )
    parser.add_argument(
        "--data-ontology",
        default=str(DEFAULT_DATA_ONTOLOGY),
        help="Data ontology Turtle file.",
    )
    parser.add_argument(
        "--rule-ontology",
        default=str(DEFAULT_RULE_ONTOLOGY),
        help="Rule ontology Turtle file.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Show detailed progress and save progress.jsonl in the output directory.",
    )
    return parser


def build_consistency_parser() -> argparse.ArgumentParser:
    """Create the Cross Review subcommand parser."""

    parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents consistency",
        description="Cross-validate Workflow, Data, and Rule RDF.",
    )
    parser.add_argument("--workflow", default=str(DEFAULT_WORKFLOW_RDF))
    parser.add_argument("--data", default=str(DEFAULT_DATA_RDF))
    parser.add_argument("--rule", default=str(DEFAULT_RULE_RDF))
    parser.add_argument(
        "--workflow-validation",
        default=str(DEFAULT_WORKFLOW_VALIDATION),
    )
    parser.add_argument("--data-validation", default=str(DEFAULT_DATA_VALIDATION))
    parser.add_argument("--rule-validation", default=str(DEFAULT_RULE_VALIDATION))
    parser.add_argument("--output-dir", default="outputs/consistency")
    parser.add_argument(
        "--workflow-ontology",
        default=str(DEFAULT_WORKFLOW_ONTOLOGY),
    )
    parser.add_argument("--data-ontology", default=str(DEFAULT_DATA_ONTOLOGY))
    parser.add_argument("--rule-ontology", default=str(DEFAULT_RULE_ONTOLOGY))
    return parser


def build_human_review_parser() -> argparse.ArgumentParser:
    """Create the standalone Human Review subcommand parser."""

    parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents human-review",
        description="Interactively review Consistency findings.",
    )
    parser.add_argument("--workflow", default=str(DEFAULT_REVIEW_WORKFLOW_RDF))
    parser.add_argument("--data", default=str(DEFAULT_REVIEW_DATA_RDF))
    parser.add_argument("--rule", default=str(DEFAULT_REVIEW_RULE_RDF))
    parser.add_argument(
        "--consistency-evaluation",
        default=str(DEFAULT_REVIEW_CONSISTENCY_EVALUATION),
    )
    parser.add_argument("--output", default=str(DEFAULT_HUMAN_REVIEW_OUTPUT))
    parser.add_argument("--reviewer", default=None)
    return parser


def build_run_parser() -> argparse.ArgumentParser:
    """Create the End-to-End Controller subcommand parser."""

    parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents run",
        description="Run Scenario through interactive Human Review from one PDF.",
    )
    parser.add_argument("--pdf", required=True, help="Input business document PDF.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Root directory for all artifacts. Defaults to OUTPUT_DIR or outputs.",
    )
    parser.add_argument(
        "--scenario-ontology",
        default=str(DEFAULT_SCENARIO_ONTOLOGY),
    )
    parser.add_argument(
        "--workflow-ontology",
        default=str(DEFAULT_WORKFLOW_ONTOLOGY),
    )
    parser.add_argument("--data-ontology", default=str(DEFAULT_DATA_ONTOLOGY))
    parser.add_argument("--rule-ontology", default=str(DEFAULT_RULE_ONTOLOGY))
    parser.add_argument("--reviewer", default=None)
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """個別pipelineまたはEnd-to-End ControllerをCLIから実行する。"""

    raw_args = list(argv) if argv is not None else []
    load_dotenv()
    config = load_config_from_env()

    if raw_args[:1] == ["human-review"]:
        args = build_human_review_parser().parse_args(raw_args[1:])
        try:
            report = run_human_review(
                workflow_file=args.workflow,
                data_file=args.data,
                rule_file=args.rule,
                consistency_evaluation_file=args.consistency_evaluation,
                output_file=args.output,
                reviewer=args.reviewer,
            )
        except (
            FileNotFoundError,
            HumanReviewInputError,
            PermissionError,
            ValueError,
        ) as error:
            print(f"エラー: {error}")
            return 1
        print("Human Reviewを完了しました")
        print(f"出力: {args.output}")
        print(f"レビュー状態: {report.status.value}")
        return 0

    if raw_args[:1] == ["run"]:
        args = build_run_parser().parse_args(raw_args[1:])
        try:
            summary = run_end_to_end_controller(
                pdf_file=args.pdf,
                model=config.openai_model,
                output_dir=args.output_dir or config.output_dir,
                scenario_ontology_file=args.scenario_ontology,
                workflow_ontology_file=args.workflow_ontology,
                data_ontology_file=args.data_ontology,
                rule_ontology_file=args.rule_ontology,
                reviewer=args.reviewer,
            )
        except (FileNotFoundError, PermissionError, ValueError) as error:
            print(f"エラー: {error}")
            return 1
        print("End-to-End実行を終了しました")
        print(f"実行サマリー: {summary.output_files['run_summary']}")
        if summary.status in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_ISSUES,
        }:
            consistency_status = _stage_result(
                summary,
                ControllerStage.CONSISTENCY,
            ).pipeline_status
            print(f"Consistency評価: {consistency_status}")
            human_review_status = _stage_result(
                summary,
                ControllerStage.HUMAN_REVIEW,
            ).pipeline_status
            print(f"Human Review: {human_review_status}")
            print(
                "Final status: "
                f"{summary.final_status.value if summary.final_status else 'unknown'}"
            )
            print(f"E2E status: {summary.status.value}")
            return 0
        print(f"失敗工程: {summary.failed_stage.value if summary.failed_stage else 'unknown'}")
        print(f"エラー: {summary.error_message}")
        return 1

    if raw_args[:1] == ["workflow"]:
        args = build_workflow_parser().parse_args(raw_args[1:])
        progress = (
            ProgressReporter(log_file=Path(args.output_dir) / "progress.jsonl")
            if args.progress
            else None
        )
        try:
            result = run_workflow_pipeline(
                scenario_file=args.scenario,
                model=config.openai_model,
                pdf_file=args.pdf,
                output_dir=args.output_dir,
                ontology_file=args.ontology,
                save_debug_outputs=args.save_debug_outputs,
                progress=progress,
            )
        except WorkflowMissingOpenAIAPIKeyError as error:
            print(f"エラー: {error}")
            return 1
        except (FileNotFoundError, PermissionError, ValueError) as error:
            print(f"エラー: {error}")
            return 1
        print("Workflow RDF生成を完了しました")
        print(f"出力先: {result['output_dir']}")
        print(f"最終状態: {result['final_status']}")
        return 0

    if raw_args[:1] == ["data-rule"]:
        args = build_data_rule_parser().parse_args(raw_args[1:])
        progress = (
            ProgressReporter(log_file=Path(args.output_dir) / "progress.jsonl")
            if args.progress
            else None
        )
        try:
            result = run_data_rule_pipeline(
                scenario_file=args.scenario,
                model=config.openai_model,
                pdf_file=args.pdf,
                output_dir=args.output_dir,
                data_ontology_file=args.data_ontology,
                rule_ontology_file=args.rule_ontology,
                progress=progress,
            )
        except DataRuleMissingOpenAIAPIKeyError as error:
            print(f"エラー: {error}")
            return 1
        except (FileNotFoundError, PermissionError, ValueError) as error:
            print(f"エラー: {error}")
            return 1
        print("Data RDF / Rule RDF生成を完了しました")
        print(f"出力先: {result['output_dir']}")
        print(f"最終状態: {result['final_status']}")
        return 0

    if raw_args[:1] == ["consistency"]:
        args = build_consistency_parser().parse_args(raw_args[1:])
        try:
            result = run_consistency_pipeline(
                model=config.openai_model,
                workflow_file=args.workflow,
                data_file=args.data,
                rule_file=args.rule,
                workflow_validation_file=args.workflow_validation,
                data_validation_file=args.data_validation,
                rule_validation_file=args.rule_validation,
                output_dir=args.output_dir,
                workflow_ontology_file=args.workflow_ontology,
                data_ontology_file=args.data_ontology,
                rule_ontology_file=args.rule_ontology,
            )
        except ConsistencyMissingOpenAIAPIKeyError as error:
            print(f"エラー: {error}")
            return 1
        except (FileNotFoundError, PermissionError, ValueError) as error:
            print(f"エラー: {error}")
            return 1
        print("Cross Reviewを完了しました")
        print(f"出力先: {result['output_dir']}")
        print(f"最終状態: {result['final_status']}")
        return 0

    args = build_parser().parse_args(raw_args)
    if not args.pdf_path:
        print("システムを開始しました")
        return 0

    try:
        scenario_result = run_scenario_pipeline(
            pdf_file=args.pdf_path,
            model=config.openai_model,
            output_dir=Path(config.output_dir) / "scenario",
        )
    except ScenarioMissingOpenAIAPIKeyError as error:
        print(f"エラー: {error}")
        return 1
    except (FileNotFoundError, PermissionError, ValueError) as error:
        print(f"エラー: {error}")
        return 1

    print("システムを開始しました")
    print(f"シナリオを保存しました: {scenario_result['scenario_file']}")
    return 0
