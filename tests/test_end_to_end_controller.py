"""Tests for the Python End-to-End Controller."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from business_analysis_agents import controller
from business_analysis_agents.config import MAX_REVISION_ITERATIONS
from business_analysis_agents.models import (
    AgentName,
    ConsistencyViolationAnalysis,
    ControllerRunSummary,
    ControllerStage,
    ControllerStageResult,
    ControllerStageStatus,
    FinalizationStatus,
    HumanReviewDecision,
    HumanReviewDecisionRecord,
    HumanReviewFinalDecision,
    HumanReviewFindingResult,
    HumanReviewReport,
    HumanReviewRevisionRequest,
    RdfKind,
    ReviewStatus,
    RunStatus,
    ShaclViolation,
)


def _write_rdf_artifacts(
    directory: Path,
    rdf_kind: str,
    *,
    validation_conforms: bool = True,
    self_review_status: str = "passed",
    rdf_turtle: str | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{rdf_kind}_final.ttl").write_text(
        rdf_turtle or f"<urn:{rdf_kind}> <urn:p> <urn:o> .",
        encoding="utf-8",
    )
    (directory / f"{rdf_kind}_shapes_generated.ttl").write_text(
        "<urn:shape> <urn:p> <urn:o> .",
        encoding="utf-8",
    )
    violations = (
        []
        if validation_conforms
        else [{"focus_node": f"urn:{rdf_kind}", "message": "Unresolved"}]
    )
    (directory / f"{rdf_kind}_validation.json").write_text(
        json.dumps(
            {
                "conforms": validation_conforms,
                "shacl_conforms": validation_conforms,
                "shacl_result": {"violations": violations},
            }
        ),
        encoding="utf-8",
    )
    findings = (
        []
        if self_review_status == "passed"
        else [{"description": f"Unresolved {rdf_kind} content"}]
    )
    (directory / f"{rdf_kind}_self_review.json").write_text(
        json.dumps(
            {
                "status": self_review_status,
                "final_result": {"findings": findings},
            }
        ),
        encoding="utf-8",
    )
    (directory / f"{rdf_kind}_revision_history.json").write_text(
        "[]",
        encoding="utf-8",
    )


def _human_review_with_data_finding(
    decision: HumanReviewDecision,
    comment: str | None = None,
) -> HumanReviewReport:
    violation = ShaclViolation(
        focus_node="urn:data:payment",
        path="urn:data:relatedResource",
        constraint_component="http://www.w3.org/ns/shacl#ClassConstraintComponent",
        message="Referenced data has an incompatible type.",
        rdf_kind=RdfKind.CONSISTENCY,
    )
    analysis = ConsistencyViolationAnalysis(
        violation_index=0,
        target_resource="urn:data:payment",
        cause="The referenced resource does not have the required data type.",
        target_agent=AgentName.DATA,
        repair_instruction="Use the documented Data RDF relation.",
    )
    finding = HumanReviewFindingResult(
        finding_id="consistency-finding-0000",
        consistency_violation_index=0,
        target_resource=analysis.target_resource,
        source_violation=violation,
        source_analysis=analysis,
        decision=decision,
        supplemental_comment=comment,
    )
    approved = decision is HumanReviewDecision.APPROVE_CURRENT_RDF
    revision_requests = (
        []
        if approved
        else [
            HumanReviewRevisionRequest(
                target_agent=AgentName.DATA,
                revision_instruction=(
                    f"{analysis.repair_instruction}\n{comment}"
                    if comment
                    else analysis.repair_instruction
                ),
                source_finding_ids=[finding.finding_id],
                target_resources=[finding.target_resource],
            )
        ]
    )
    final_decision = (
        HumanReviewFinalDecision.APPROVE
        if approved
        else HumanReviewFinalDecision.REQUEST_REVISION
    )
    record = HumanReviewDecisionRecord(
        review_round=1,
        decision=final_decision,
        approved=approved,
        revision_requested=not approved,
        revision_requests=revision_requests,
    )
    return HumanReviewReport(
        status=ReviewStatus.APPROVED if approved else ReviewStatus.NEEDS_REVISION,
        input_received=True,
        final_decision=final_decision,
        approved=approved,
        revision_requested=not approved,
        review_round=1,
        consistency_status=ReviewStatus.NEEDS_REVISION,
        consistency_conforms=False,
        workflow_rdf_file="workflow.ttl",
        data_rdf_file="data.ttl",
        rule_rdf_file=None,
        consistency_evaluation_file="consistency.json",
        findings=[finding],
        revision_requests=revision_requests,
        decision_history=[record],
        summary="Reviewed one finding.",
    )


def _approved_human_review(**kwargs) -> HumanReviewReport:
    history = list(kwargs.get("decision_history") or [])
    review_round = int(kwargs.get("review_round", len(history) + 1))
    record = HumanReviewDecisionRecord(
        review_round=review_round,
        decision=HumanReviewFinalDecision.APPROVE,
        approved=True,
        revision_requested=False,
    )
    return HumanReviewReport(
        status=ReviewStatus.APPROVED,
        input_received=True,
        final_decision=HumanReviewFinalDecision.APPROVE,
        approved=True,
        revision_requested=False,
        review_round=review_round,
        consistency_status=ReviewStatus.APPROVED,
        consistency_conforms=True,
        workflow_rdf_file=str(kwargs["workflow_file"]),
        data_rdf_file=str(kwargs["data_file"]),
        rule_rdf_file=(
            str(kwargs["rule_file"])
            if kwargs.get("rule_file") is not None
            else None
        ),
        consistency_evaluation_file=str(kwargs["consistency_evaluation_file"]),
        decision_history=[*history, record],
        summary="Human explicitly approved the final RDF.",
    )


def test_end_to_end_controller_completes_without_human_revision(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Consistency violations complete the run without becoming a system error."""

    calls: list[str] = []
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")
    scenario_path = tmp_path / "scenario" / "scenario_final.ttl"

    def fake_scenario(**kwargs):
        calls.append("scenario")
        assert kwargs["pdf_file"] == pdf_path
        scenario_path.parent.mkdir(parents=True)
        scenario_path.write_text("<urn:s> <urn:p> <urn:o> .", encoding="utf-8")
        return {
            "output_dir": str(scenario_path.parent),
            "final_status": "completed",
            "scenario_file": str(scenario_path),
            "ontology_file": "scenario.ttl",
        }

    def fake_workflow(**kwargs):
        calls.append("workflow")
        assert kwargs["scenario_file"] == str(scenario_path)
        assert kwargs["pdf_file"] == pdf_path
        assert kwargs["output_dir"] == tmp_path / "workflow"
        workflow_dir = kwargs["output_dir"]
        _write_rdf_artifacts(workflow_dir, "workflow")
        return {"output_dir": str(kwargs["output_dir"]), "final_status": "completed"}

    def fake_data_rule(**kwargs):
        calls.append("data")
        assert kwargs["scenario_file"] == str(scenario_path)
        assert kwargs["pdf_file"] == pdf_path
        assert kwargs["output_dir"] == tmp_path / "data_rule"
        data_rule_dir = kwargs["output_dir"]
        assert kwargs["include_rule"] is False
        _write_rdf_artifacts(data_rule_dir, "data")
        kwargs["progress"].phase(3, 5, "Data RDF", "data")
        return {"output_dir": str(kwargs["output_dir"]), "final_status": "completed"}

    def fake_consistency(**kwargs):
        calls.append("consistency")
        assert kwargs["workflow_dir"] == tmp_path / "workflow"
        assert kwargs["data_rule_dir"] == tmp_path / "data_rule"
        assert kwargs["consistency_dir"] == tmp_path / "consistency"
        return (
            {
                "output_dir": str(kwargs["consistency_dir"]),
                "final_status": "completed",
            },
            {"status": "completed", "revision_rounds": 0},
        )

    def fake_human_review(**kwargs):
        calls.append("human_review")
        assert kwargs["consistency_evaluation_file"] == (
            tmp_path / "consistency" / "consistency_evaluation.json"
        )
        assert kwargs["output_file"] == (
            tmp_path / "human_review" / "human_review.json"
        )
        return _approved_human_review(**kwargs)

    monkeypatch.setattr(controller, "run_scenario_pipeline", fake_scenario)
    monkeypatch.setattr(controller, "run_workflow_pipeline", fake_workflow)
    monkeypatch.setattr(controller, "run_data_rule_pipeline", fake_data_rule)
    monkeypatch.setattr(controller, "run_consistency_revision_loop", fake_consistency)
    monkeypatch.setattr(controller, "run_human_review", fake_human_review)

    summary = controller.run_end_to_end_controller(
        pdf_file=pdf_path,
        model="test-model",
        output_dir=tmp_path,
    )

    assert calls == [
        "scenario",
        "workflow",
        "data",
        "consistency",
        "human_review",
    ]
    assert summary.status is RunStatus.COMPLETED
    assert summary.completed is True
    assert summary.failed_stage is None
    assert [stage.status for stage in summary.stages] == [
        ControllerStageStatus.COMPLETED,
        ControllerStageStatus.COMPLETED,
        ControllerStageStatus.COMPLETED,
        ControllerStageStatus.COMPLETED,
        ControllerStageStatus.COMPLETED,
    ]
    saved = json.loads(
        (tmp_path / "controller" / "run_summary.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "completed"
    assert saved["completed"] is True
    assert saved["stages"][3]["pipeline_status"] == "completed"
    assert saved["stages"][4]["pipeline_status"] == "approved"
    assert saved["final_status"] == "completed_after_human_approval"
    assert saved["human_review_input_received"] is True
    assert saved["human_review_approved"] is True
    assert "consistency_evaluation" in saved["output_files"]
    assert "human_review" in saved["output_files"]
    assert "final_summary" in saved["output_files"]
    assert "final_workflow_rdf" in saved["output_files"]
    assert "rule_rdf" not in saved["output_files"]
    final_summary = json.loads(
        (tmp_path / "final" / "final_summary.json").read_text(encoding="utf-8")
    )
    assert final_summary["final_status"] == "completed_after_human_approval"
    assert final_summary["human_review_revision_performed"] is False
    progress_output = capsys.readouterr().out
    for index, name in enumerate(
        (
            "Scenario RDF",
            "Workflow RDF",
            "Data RDF",
            "Cross Consistency",
            "Human Review / Finalization",
        ),
        start=1,
    ):
        assert f"[Phase {index}/5] {name}" in progress_output
    assert "Rule RDF" not in progress_output
    assert (tmp_path / "controller" / "progress.jsonl").exists()


def test_end_to_end_human_revision_returns_to_human_review(
    monkeypatch,
    tmp_path,
) -> None:
    """A requested revision is Cross-checked and explicitly approved next round."""

    scenario_path = tmp_path / "scenario" / "scenario_final.ttl"

    def fake_scenario(**kwargs):
        scenario_path.parent.mkdir(parents=True)
        scenario_path.write_text("<urn:s> <urn:p> <urn:o> .", encoding="utf-8")
        return {"final_status": "completed", "scenario_file": str(scenario_path)}

    def fake_workflow(**kwargs):
        _write_rdf_artifacts(kwargs["output_dir"], "workflow")
        return {"final_status": "completed"}

    def fake_data_rule(**kwargs):
        assert kwargs["include_rule"] is False
        _write_rdf_artifacts(kwargs["output_dir"], "data")
        return {"final_status": "completed"}

    monkeypatch.setattr(controller, "run_scenario_pipeline", fake_scenario)
    monkeypatch.setattr(controller, "run_workflow_pipeline", fake_workflow)
    monkeypatch.setattr(controller, "run_data_rule_pipeline", fake_data_rule)
    monkeypatch.setattr(
        controller,
        "run_consistency_revision_loop",
        lambda **kwargs: (
            {"final_status": "completed", "evaluation": {"violations": []}},
            {"status": "completed", "revision_rounds": 0},
        ),
    )

    review_calls: list[int] = []

    def fake_human_review(**kwargs):
        review_round = kwargs["review_round"]
        review_calls.append(review_round)
        history = list(kwargs["decision_history"])
        if review_round == 2:
            return _approved_human_review(**kwargs)
        request = HumanReviewRevisionRequest(
            target_agent=AgentName.DATA,
            revision_instruction="Correct the documented Data RDF relation.",
        )
        record = HumanReviewDecisionRecord(
            review_round=1,
            decision=HumanReviewFinalDecision.REQUEST_REVISION,
            approved=False,
            revision_requested=True,
            revision_requests=[request],
        )
        return HumanReviewReport(
            status=ReviewStatus.NEEDS_REVISION,
            input_received=True,
            final_decision=HumanReviewFinalDecision.REQUEST_REVISION,
            approved=False,
            revision_requested=True,
            review_round=1,
            consistency_status=ReviewStatus.APPROVED,
            consistency_conforms=True,
            workflow_rdf_file=str(kwargs["workflow_file"]),
            data_rdf_file=str(kwargs["data_file"]),
            rule_rdf_file=None,
            consistency_evaluation_file=str(kwargs["consistency_evaluation_file"]),
            revision_requests=[request],
            decision_history=[*history, record],
            summary="Human requested revision.",
        )

    revision_calls: list[str] = []

    def fake_human_revision(**kwargs):
        revision_calls.append(kwargs["human_review"].revision_requests[0].target_agent.value)
        return {
            "revision_performed": True,
            "repair_bundles": [{"target_agent": "data"}],
            "revision_results": [{"target_agent": "data", "final_status": "completed"}],
            "individual_checks_passed": True,
            "consistency_result": {
                "final_status": "completed",
                "evaluation": {"violations": []},
            },
        }

    monkeypatch.setattr(controller, "run_human_review", fake_human_review)
    monkeypatch.setattr(controller, "run_human_review_revision", fake_human_revision)

    summary = controller.run_end_to_end_controller(
        pdf_file=tmp_path / "manual.pdf",
        model="test-model",
        output_dir=tmp_path,
    )

    assert review_calls == [1, 2]
    assert revision_calls == ["data"]
    assert summary.status is RunStatus.COMPLETED
    assert summary.final_status is FinalizationStatus.COMPLETED_AFTER_HUMAN_REVISION
    assert summary.human_review_input_received is True
    assert summary.human_review_approved is True
    assert summary.human_review_revision_requested is True
    assert summary.human_review_approved_after_revision is True
    assert summary.human_review_rounds == 2


@pytest.mark.parametrize("issue_kind", ["workflow", "data", "cross"])
def test_end_to_end_controller_continues_with_quality_issues(
    monkeypatch,
    tmp_path,
    issue_kind,
    capsys,
) -> None:
    """Individual and Cross quality issues still reach finalization."""

    calls: list[str] = []
    scenario_path = tmp_path / "scenario" / "scenario_final.ttl"

    def fake_scenario(**kwargs):
        scenario_path.parent.mkdir(parents=True)
        scenario_path.write_text("<urn:s> <urn:p> <urn:o> .", encoding="utf-8")
        return {"final_status": "completed", "scenario_file": str(scenario_path)}

    def fake_workflow(**kwargs):
        calls.append("workflow")
        _write_rdf_artifacts(
            kwargs["output_dir"],
            "workflow",
            self_review_status=(
                "max_iterations" if issue_kind == "workflow" else "passed"
            ),
        )
        return {
            "final_status": (
                "needs_review" if issue_kind == "workflow" else "completed"
            )
        }

    def fake_data_rule(**kwargs):
        calls.append("data")
        assert kwargs["include_rule"] is False
        _write_rdf_artifacts(
            kwargs["output_dir"],
            "data",
            self_review_status=(
                "max_iterations" if issue_kind == "data" else "passed"
            ),
        )
        return {
            "final_status": "needs_review" if issue_kind == "data" else "completed"
        }

    def fake_consistency(**kwargs):
        calls.append("consistency")
        if issue_kind == "cross":
            return (
                {
                    "final_status": "needs_revision",
                    "evaluation": {"violations": [{"message": "Unresolved Cross"}]},
                },
                {"status": "max_iterations", "revision_rounds": 3},
            )
        return (
            {"final_status": "completed", "evaluation": {"violations": []}},
            {"status": "completed", "revision_rounds": 0},
        )

    def fake_human_review(**kwargs):
        calls.append("human_review")
        assert "individual_rdf_issues" not in kwargs
        if issue_kind == "cross":
            report = _human_review_with_data_finding(
                HumanReviewDecision.APPROVE_CURRENT_RDF
            ).model_copy(
                update={
                    "status": ReviewStatus.APPROVED,
                    "workflow_rdf_file": str(kwargs["workflow_file"]),
                    "data_rdf_file": str(kwargs["data_file"]),
                    "rule_rdf_file": None,
                    "consistency_evaluation_file": str(
                        kwargs["consistency_evaluation_file"]
                    ),
                }
            )
        else:
            report = _approved_human_review(**kwargs)
        Path(kwargs["output_file"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_file"]).write_text(
            report.model_dump_json(), encoding="utf-8"
        )
        return report

    monkeypatch.setattr(controller, "run_scenario_pipeline", fake_scenario)
    monkeypatch.setattr(controller, "run_workflow_pipeline", fake_workflow)
    monkeypatch.setattr(controller, "run_data_rule_pipeline", fake_data_rule)
    monkeypatch.setattr(controller, "run_consistency_revision_loop", fake_consistency)
    monkeypatch.setattr(controller, "run_human_review", fake_human_review)

    summary = controller.run_end_to_end_controller(
        pdf_file=tmp_path / "manual.pdf",
        model="test-model",
        output_dir=tmp_path,
    )

    assert calls == ["workflow", "data", "consistency", "human_review"]
    expected_status = (
        RunStatus.COMPLETED_WITH_ISSUES
        if issue_kind == "cross"
        else RunStatus.COMPLETED
    )
    assert summary.status is expected_status
    assert summary.completed is True
    assert summary.fatal_error is False
    assert summary.human_review_required is True
    if issue_kind == "cross":
        assert summary.cross_consistency_finding_count == 1
    else:
        assert summary.individual_rdf_issues[
            issue_kind
        ].self_review_finding_count == 1
    saved_review = json.loads(
        (tmp_path / "human_review" / "human_review.json").read_text(
            encoding="utf-8"
        )
    )
    assert "individual_rdf_issues" not in saved_review
    saved_summary = json.loads(
        (tmp_path / "controller" / "run_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved_summary["status"] == expected_status.value
    assert saved_summary["fatal_error"] is False
    assert saved_summary["human_review_required"] is True
    progress_output = capsys.readouterr().out
    if issue_kind == "cross":
        assert "Cross Consistency completed with unresolved findings" in progress_output
        assert "Continuing to Human Review" in progress_output
    else:
        assert f"{issue_kind.title()} completed with issues" not in progress_output


def _run_human_review_revision(
    monkeypatch,
    tmp_path,
    *,
    targeted_status: str,
    cross_status: str,
    decision: HumanReviewDecision = HumanReviewDecision.APPROVE_FINDING,
    comment: str | None = None,
) -> tuple[dict, list[str]]:
    calls: list[str] = []

    def fake_targeted(**kwargs):
        calls.append("targeted")
        assert kwargs["target_agent"] is AgentName.DATA
        bundle = kwargs["repair_bundle"]
        assert bundle["source"] == "human_review"
        assert bundle["items"][0]["human_decision"] == "request_revision"
        if comment:
            assert comment in bundle["items"][0]["revision_instruction"]
        assert kwargs["rule_file"] is None
        return {"target_agent": "data", "final_status": targeted_status}

    def fake_consistency(**kwargs):
        calls.append("consistency")
        assert kwargs["cross_shacl_file"] == (
            tmp_path / "consistency" / "consistency_shapes_generated.ttl"
        )
        assert kwargs["expected_cross_shapes_hash"] == "fixed-cross-hash"
        return {
            "final_status": cross_status,
            "cross_shapes_hash": "fixed-cross-hash",
        }

    monkeypatch.setattr(controller, "run_targeted_rdf_revision", fake_targeted)
    monkeypatch.setattr(controller, "run_consistency_pipeline", fake_consistency)
    result = controller.run_human_review_revision(
        human_review=_human_review_with_data_finding(decision, comment),
        consistency_result={
            "final_status": "needs_revision",
            "cross_shapes_hash": "fixed-cross-hash",
        },
        consistency_iteration=4,
        model="test-model",
        pdf_file=tmp_path / "manual.pdf",
        scenario_file=tmp_path / "scenario.ttl",
        workflow_dir=tmp_path / "workflow",
        data_rule_dir=tmp_path / "data_rule",
        consistency_dir=tmp_path / "consistency",
        human_review_dir=tmp_path / "human_review",
        workflow_ontology_file=tmp_path / "workflow-ontology.ttl",
        data_ontology_file=tmp_path / "data-ontology.ttl",
        rule_ontology_file=tmp_path / "rule-ontology.ttl",
    )
    return result, calls


def test_human_review_revision_revalidates_and_completes(monkeypatch, tmp_path) -> None:
    """Human context drives one targeted revision and fixed Cross re-validation."""

    result, calls = _run_human_review_revision(
        monkeypatch,
        tmp_path,
        targeted_status="completed",
        cross_status="completed",
        decision=HumanReviewDecision.PROVIDE_CONTEXT,
        comment="Use urn:data:payment.",
    )

    assert calls == ["targeted", "consistency"]
    assert result["revision_performed"] is True
    assert result["final_status"] == "completed_after_human_revision"


def test_human_review_revision_records_remaining_cross_violation(
    monkeypatch,
    tmp_path,
) -> None:
    """A valid targeted RDF can still end with Cross issues unresolved."""

    result, calls = _run_human_review_revision(
        monkeypatch,
        tmp_path,
        targeted_status="completed",
        cross_status="needs_revision",
    )

    assert calls == ["targeted", "consistency"]
    assert result["final_status"] == "unresolved_after_human_review"


def test_human_review_revision_continues_after_individual_quality_issue(
    monkeypatch,
    tmp_path,
) -> None:
    """A parseable targeted RDF reaches Cross validation despite quality issues."""

    result, calls = _run_human_review_revision(
        monkeypatch,
        tmp_path,
        targeted_status="needs_review",
        cross_status="completed",
    )

    assert calls == ["targeted", "consistency"]
    assert result["individual_checks_passed"] is False
    assert result["final_status"] == "completed_after_human_revision"


def test_end_to_end_controller_stops_after_stage_exception(monkeypatch, tmp_path) -> None:
    """A failed stage is recorded and all later stages remain unexecuted."""

    scenario_path = tmp_path / "scenario" / "scenario_final.ttl"
    scenario_path.parent.mkdir(parents=True)
    scenario_path.write_text("<urn:s> <urn:p> <urn:o> .", encoding="utf-8")

    monkeypatch.setattr(
        controller,
        "run_scenario_pipeline",
        lambda **kwargs: {
            "output_dir": str(scenario_path.parent),
            "final_status": "completed",
            "scenario_file": str(scenario_path),
            "ontology_file": "scenario.ttl",
        },
    )

    def fail_workflow(**kwargs):
        raise RuntimeError("workflow failed")

    def must_not_run(**kwargs):
        raise AssertionError("A later pipeline was executed")

    monkeypatch.setattr(controller, "run_workflow_pipeline", fail_workflow)
    monkeypatch.setattr(controller, "run_data_rule_pipeline", must_not_run)
    monkeypatch.setattr(controller, "run_consistency_revision_loop", must_not_run)
    monkeypatch.setattr(controller, "run_human_review", must_not_run)

    summary = controller.run_end_to_end_controller(
        pdf_file=tmp_path / "manual.pdf",
        model="test-model",
        output_dir=tmp_path,
    )

    assert summary.status is RunStatus.FATAL_FAILED
    assert summary.completed is False
    assert summary.failed_stage is ControllerStage.WORKFLOW
    assert summary.error_message == "workflow failed"
    assert [stage.status for stage in summary.stages] == [
        ControllerStageStatus.COMPLETED,
        ControllerStageStatus.FATAL_FAILED,
        ControllerStageStatus.SKIPPED,
        ControllerStageStatus.SKIPPED,
        ControllerStageStatus.SKIPPED,
    ]
    saved = json.loads(
        (tmp_path / "controller" / "run_summary.json").read_text(encoding="utf-8")
    )
    assert saved["failed_stage"] == "workflow"
    assert saved["stages"][1]["error_type"] == "RuntimeError"


@pytest.mark.parametrize("failure_mode", ["missing", "invalid_turtle"])
def test_end_to_end_controller_stops_when_required_rdf_is_unusable(
    monkeypatch,
    tmp_path,
    failure_mode,
) -> None:
    """Missing and non-parseable Workflow RDF artifacts are fatal."""

    scenario_path = tmp_path / "scenario" / "scenario_final.ttl"
    scenario_path.parent.mkdir(parents=True)
    scenario_path.write_text("<urn:s> <urn:p> <urn:o> .", encoding="utf-8")
    monkeypatch.setattr(
        controller,
        "run_scenario_pipeline",
        lambda **kwargs: {
            "output_dir": str(scenario_path.parent),
            "final_status": "completed",
            "scenario_file": str(scenario_path),
            "ontology_file": "scenario.ttl",
        },
    )
    def fake_workflow(**kwargs):
        if failure_mode == "invalid_turtle":
            _write_rdf_artifacts(
                kwargs["output_dir"],
                "workflow",
                rdf_turtle="this is not Turtle",
            )
        return {
            "output_dir": str(kwargs["output_dir"]),
            "final_status": "needs_review",
        }

    monkeypatch.setattr(controller, "run_workflow_pipeline", fake_workflow)

    def must_not_run(**kwargs):
        raise AssertionError("An unvalidated RDF was passed to a later pipeline")

    monkeypatch.setattr(controller, "run_data_rule_pipeline", must_not_run)
    monkeypatch.setattr(controller, "run_consistency_revision_loop", must_not_run)
    monkeypatch.setattr(controller, "run_human_review", must_not_run)

    summary = controller.run_end_to_end_controller(
        pdf_file=tmp_path / "manual.pdf",
        model="test-model",
        output_dir=tmp_path,
    )

    workflow_stage = next(
        stage for stage in summary.stages if stage.stage is ControllerStage.WORKFLOW
    )
    assert summary.status is RunStatus.FATAL_FAILED
    assert summary.failed_stage is ControllerStage.WORKFLOW
    assert workflow_stage.status is ControllerStageStatus.FATAL_FAILED
    assert workflow_stage.pipeline_status == "completed"


def test_end_to_end_controller_records_human_review_failure(
    monkeypatch,
    tmp_path,
) -> None:
    """Only an actual Human Review processing error fails the final stage."""

    scenario_path = tmp_path / "scenario" / "scenario_final.ttl"
    scenario_path.parent.mkdir(parents=True)
    scenario_path.write_text("<urn:s> <urn:p> <urn:o> .", encoding="utf-8")
    monkeypatch.setattr(
        controller,
        "run_scenario_pipeline",
        lambda **kwargs: {
            "output_dir": str(scenario_path.parent),
            "final_status": "completed",
            "scenario_file": str(scenario_path),
            "ontology_file": "scenario.ttl",
        },
    )
    def fake_workflow(**kwargs):
        _write_rdf_artifacts(kwargs["output_dir"], "workflow")
        return {"final_status": "completed"}

    def fake_data_rule(**kwargs):
        _write_rdf_artifacts(kwargs["output_dir"], "data")
        _write_rdf_artifacts(kwargs["output_dir"], "rule")
        return {"final_status": "completed"}

    monkeypatch.setattr(controller, "run_workflow_pipeline", fake_workflow)
    monkeypatch.setattr(controller, "run_data_rule_pipeline", fake_data_rule)
    monkeypatch.setattr(
        controller,
        "run_consistency_revision_loop",
        lambda **kwargs: (
            {"final_status": "needs_revision"},
            {"status": "max_iterations"},
        ),
    )

    def fail_human_review(**kwargs):
        raise ValueError("invalid review input")

    monkeypatch.setattr(controller, "run_human_review", fail_human_review)

    summary = controller.run_end_to_end_controller(
        pdf_file=tmp_path / "manual.pdf",
        model="test-model",
        output_dir=tmp_path,
    )

    assert summary.status is RunStatus.FATAL_FAILED
    assert summary.failed_stage is ControllerStage.HUMAN_REVIEW
    assert summary.stages[3].status is ControllerStageStatus.COMPLETED_WITH_ISSUES
    assert summary.stages[4].status is ControllerStageStatus.FATAL_FAILED
    assert summary.stages[4].error_message == "invalid review input"


def test_run_subcommand_uses_fixed_revision_limit(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """The integrated CLI returns zero even when Consistency needs revision."""

    summary = ControllerRunSummary(
        input_pdf=str(tmp_path / "manual.pdf"),
        started_at=datetime.now().astimezone(),
        finished_at=datetime.now().astimezone(),
        status=RunStatus.COMPLETED_WITH_ISSUES,
        completed=True,
        stages=[
            ControllerStageResult(
                stage=ControllerStage.CONSISTENCY,
                status=ControllerStageStatus.COMPLETED_WITH_ISSUES,
                pipeline_status="needs_revision",
            ),
            ControllerStageResult(
                stage=ControllerStage.HUMAN_REVIEW,
                status=ControllerStageStatus.COMPLETED,
                pipeline_status="needs_revision",
            ),
        ],
        output_files={"run_summary": str(tmp_path / "run_summary.json")},
    )
    received = {}

    def fake_controller(**kwargs):
        received.update(kwargs)
        return summary

    monkeypatch.setattr(controller, "run_end_to_end_controller", fake_controller)

    assert controller.run(["run", "--pdf", "manual.pdf"]) == 0
    assert not any(key.startswith("max_") for key in received)
    for parser_factory in (
        controller.build_run_parser,
        controller.build_workflow_parser,
        controller.build_data_rule_parser,
    ):
        assert "--max-" not in parser_factory().format_help()
    output = capsys.readouterr().out
    assert "Consistency評価: needs_revision" in output
    assert "Human Review: needs_revision" in output


@pytest.mark.parametrize(
    ("command", "pipeline_name"),
    [
        ("workflow", "run_workflow_pipeline"),
        ("data-rule", "run_data_rule_pipeline"),
    ],
)
@pytest.mark.parametrize("progress_enabled", [False, True])
def test_individual_cli_optionally_passes_progress_reporter(
    monkeypatch,
    tmp_path,
    capsys,
    command,
    pipeline_name,
    progress_enabled,
) -> None:
    """Individual RDF CLIs enable existing progress reporting only on request."""

    received = {}

    def fake_pipeline(**kwargs):
        received.update(kwargs)
        progress = kwargs["progress"]
        if progress is not None:
            progress.report(
                phase=command,
                step="test",
                status=controller.ProgressStatus.RUNNING,
                message=f"{command} detailed progress",
            )
        return {"output_dir": kwargs["output_dir"], "final_status": "completed"}

    monkeypatch.setattr(controller, pipeline_name, fake_pipeline)
    output_dir = tmp_path / command
    args = [
        command,
        "--pdf",
        "sample.pdf",
        "--scenario",
        "scenario_final.ttl",
        "--output-dir",
        str(output_dir),
    ]
    if progress_enabled:
        args.append("--progress")

    assert controller.run(args) == 0

    progress = received["progress"]
    if progress_enabled:
        assert isinstance(progress, controller.ProgressReporter)
        assert progress.log_file == output_dir / "progress.jsonl"
        assert progress.log_file.exists()
    else:
        assert progress is None
        assert not (output_dir / "progress.jsonl").exists()

    output = capsys.readouterr().out
    assert (f"[RUN] {command} detailed progress" in output) is progress_enabled

    parser = (
        controller.build_workflow_parser()
        if command == "workflow"
        else controller.build_data_rule_parser()
    )
    assert "--progress" in parser.format_help()


def test_consistency_revision_loop_groups_targets_and_reuses_cross_shacl(
    monkeypatch,
    tmp_path,
) -> None:
    """Two findings for one RDF produce one revision and a fixed-SHACL recheck."""

    workflow_dir = tmp_path / "workflow"
    data_rule_dir = tmp_path / "data_rule"
    consistency_dir = tmp_path / "consistency"
    workflow_dir.mkdir()
    data_rule_dir.mkdir()
    consistency_dir.mkdir()
    (workflow_dir / "workflow_final.ttl").write_text(
        "<urn:workflow> <urn:p> <urn:o> .",
        encoding="utf-8",
    )
    (data_rule_dir / "data_final.ttl").write_text(
        "<urn:data> <urn:p> <urn:o> .",
        encoding="utf-8",
    )
    cross_calls = 0
    revision_calls = 0

    violations = [
        {
            "focus_node": f"urn:data:{index}",
            "message": "Missing cross reference",
            "severity": "error",
            "rdf_kind": "consistency",
        }
        for index in range(2)
    ]
    analyses = [
        {
            "violation_index": index,
            "target_resource": f"urn:data:{index}",
            "cause": f"Cause {index}",
            "target_agent": "data",
            "repair_instruction": f"Repair instruction {index}",
        }
        for index in range(2)
    ]

    def fake_consistency(**kwargs):
        nonlocal cross_calls
        cross_calls += 1
        assert kwargs["rule_file"] is None
        assert kwargs["rule_validation_file"] is None
        assert kwargs["rule_ontology_file"] is None
        if cross_calls == 1:
            assert "cross_shacl_file" not in kwargs
            return {
                "final_status": "needs_revision",
                "cross_shapes_hash": "fixed-cross-hash",
                "evaluation": {
                    "conforms": False,
                    "can_auto_repair": True,
                    "violations": violations,
                    "violation_analyses": analyses,
                },
            }
        assert kwargs["cross_shacl_file"] == (
            consistency_dir / "consistency_shapes_generated.ttl"
        )
        assert kwargs["expected_cross_shapes_hash"] == "fixed-cross-hash"
        return {
            "final_status": "completed",
            "cross_shapes_hash": "fixed-cross-hash",
            "evaluation": {
                "conforms": True,
                "can_auto_repair": False,
                "violations": [],
                "violation_analyses": [],
            },
        }

    def fake_revision(**kwargs):
        nonlocal revision_calls
        revision_calls += 1
        assert kwargs["target_agent"].value == "data"
        assert kwargs["rule_file"] is None
        bundle = kwargs["repair_bundle"]
        assert bundle["violation_indices"] == [0, 1]
        assert len(bundle["items"]) == 2
        assert bundle["repair_instructions"] == [
            "Repair instruction 0",
            "Repair instruction 1",
        ]
        (data_rule_dir / "data_final.ttl").write_text(
            "<urn:data-revised> <urn:p> <urn:o> .",
            encoding="utf-8",
        )
        return {
            "target_agent": "data",
            "final_status": "completed",
            "rdf_hash": "revised-data-hash",
            "validation": {"conforms": True},
            "self_review": {"status": "passed"},
        }

    monkeypatch.setattr(controller, "run_consistency_pipeline", fake_consistency)
    monkeypatch.setattr(controller, "run_targeted_rdf_revision", fake_revision)

    final_result, history = controller.run_consistency_revision_loop(
        model="test-model",
        pdf_file=tmp_path / "manual.pdf",
        scenario_file=tmp_path / "scenario.ttl",
        workflow_dir=workflow_dir,
        data_rule_dir=data_rule_dir,
        consistency_dir=consistency_dir,
        workflow_ontology_file=tmp_path / "workflow-ontology.ttl",
        data_ontology_file=tmp_path / "data-ontology.ttl",
        rule_ontology_file=None,
    )

    assert final_result["final_status"] == "completed"
    assert history["status"] == "completed"
    assert history["revision_rounds"] == 1
    assert cross_calls == 2
    assert revision_calls == 1
    saved = json.loads(
        (consistency_dir / "consistency_revision_history.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["cross_shapes_hash"] == "fixed-cross-hash"
    assert saved["max_cross_revision_iterations"] == MAX_REVISION_ITERATIONS
    assert len(saved["iterations"]) == 2
    assert saved["iterations"][0]["revision_results"][0][
        "target_agent"
    ] == "data"
