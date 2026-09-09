"""Tests for the Python End-to-End Controller."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from business_analysis_agents import controller
from business_analysis_agents.models import (
    ControllerRunSummary,
    ControllerStage,
    ControllerStageResult,
    ControllerStageStatus,
    RunStatus,
)


def test_end_to_end_controller_connects_all_pipelines_and_accepts_inconsistency(
    monkeypatch,
    tmp_path,
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
        return {"output_dir": str(kwargs["output_dir"]), "final_status": "completed"}

    def fake_data_rule(**kwargs):
        calls.append("data_rule")
        assert kwargs["scenario_file"] == str(scenario_path)
        assert kwargs["pdf_file"] == pdf_path
        assert kwargs["output_dir"] == tmp_path / "data_rule"
        return {"output_dir": str(kwargs["output_dir"]), "final_status": "completed"}

    def fake_consistency(**kwargs):
        calls.append("consistency")
        assert kwargs["workflow_file"] == tmp_path / "workflow" / "workflow_final.ttl"
        assert kwargs["data_file"] == tmp_path / "data_rule" / "data_final.ttl"
        assert kwargs["rule_file"] == tmp_path / "data_rule" / "rule_final.ttl"
        assert kwargs["workflow_validation_file"] == (
            tmp_path / "workflow" / "workflow_validation.json"
        )
        return {
            "output_dir": str(kwargs["output_dir"]),
            "final_status": "needs_revision",
        }

    monkeypatch.setattr(controller, "run_scenario_pipeline", fake_scenario)
    monkeypatch.setattr(controller, "run_workflow_pipeline", fake_workflow)
    monkeypatch.setattr(controller, "run_data_rule_pipeline", fake_data_rule)
    monkeypatch.setattr(controller, "run_consistency_pipeline", fake_consistency)

    summary = controller.run_end_to_end_controller(
        pdf_file=pdf_path,
        model="test-model",
        output_dir=tmp_path,
    )

    assert calls == ["scenario", "workflow", "data_rule", "consistency"]
    assert summary.status is RunStatus.COMPLETED
    assert summary.completed is True
    assert summary.failed_stage is None
    assert [stage.status for stage in summary.stages] == [
        ControllerStageStatus.COMPLETED,
        ControllerStageStatus.COMPLETED,
        ControllerStageStatus.COMPLETED,
        ControllerStageStatus.NEEDS_REVIEW,
    ]
    saved = json.loads(
        (tmp_path / "controller" / "run_summary.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "completed"
    assert saved["completed"] is True
    assert saved["stages"][3]["pipeline_status"] == "needs_revision"
    assert "consistency_evaluation" in saved["output_files"]


def test_end_to_end_controller_stops_after_stage_exception(monkeypatch, tmp_path) -> None:
    """A failed stage is recorded and all later stages remain unexecuted."""

    scenario_path = tmp_path / "scenario" / "scenario_final.ttl"

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
    monkeypatch.setattr(controller, "run_consistency_pipeline", must_not_run)

    summary = controller.run_end_to_end_controller(
        pdf_file=tmp_path / "manual.pdf",
        model="test-model",
        output_dir=tmp_path,
    )

    assert summary.status is RunStatus.FAILED
    assert summary.completed is False
    assert summary.failed_stage is ControllerStage.WORKFLOW
    assert summary.error_message == "workflow failed"
    assert [stage.status for stage in summary.stages] == [
        ControllerStageStatus.COMPLETED,
        ControllerStageStatus.FAILED,
        ControllerStageStatus.SKIPPED,
        ControllerStageStatus.SKIPPED,
    ]
    saved = json.loads(
        (tmp_path / "controller" / "run_summary.json").read_text(encoding="utf-8")
    )
    assert saved["failed_stage"] == "workflow"
    assert saved["stages"][1]["error_type"] == "RuntimeError"


def test_end_to_end_controller_stops_when_individual_validation_needs_review(
    monkeypatch,
    tmp_path,
) -> None:
    """An unvalidated Workflow RDF is not passed to Data/Rule or Consistency."""

    scenario_path = tmp_path / "scenario" / "scenario_final.ttl"
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
    monkeypatch.setattr(
        controller,
        "run_workflow_pipeline",
        lambda **kwargs: {
            "output_dir": str(kwargs["output_dir"]),
            "final_status": "needs_review",
        },
    )

    def must_not_run(**kwargs):
        raise AssertionError("An unvalidated RDF was passed to a later pipeline")

    monkeypatch.setattr(controller, "run_data_rule_pipeline", must_not_run)
    monkeypatch.setattr(controller, "run_consistency_pipeline", must_not_run)

    summary = controller.run_end_to_end_controller(
        pdf_file=tmp_path / "manual.pdf",
        model="test-model",
        output_dir=tmp_path,
    )

    workflow_stage = next(
        stage for stage in summary.stages if stage.stage is ControllerStage.WORKFLOW
    )
    assert summary.status is RunStatus.FAILED
    assert summary.failed_stage is ControllerStage.WORKFLOW
    assert workflow_stage.status is ControllerStageStatus.NEEDS_REVIEW
    assert workflow_stage.pipeline_status == "needs_review"


def test_run_subcommand_returns_success_for_completed_cross_review(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """The integrated CLI returns zero even when Consistency needs revision."""

    summary = ControllerRunSummary(
        input_pdf=str(tmp_path / "manual.pdf"),
        started_at=datetime.now().astimezone(),
        finished_at=datetime.now().astimezone(),
        status=RunStatus.COMPLETED,
        completed=True,
        stages=[
            ControllerStageResult(
                stage=ControllerStage.CONSISTENCY,
                status=ControllerStageStatus.NEEDS_REVIEW,
                pipeline_status="needs_revision",
            )
        ],
        output_files={"run_summary": str(tmp_path / "run_summary.json")},
    )
    monkeypatch.setattr(
        controller,
        "run_end_to_end_controller",
        lambda **kwargs: summary,
    )

    assert controller.run(["run", "--pdf", "manual.pdf"]) == 0
    assert "Consistency評価: needs_revision" in capsys.readouterr().out
