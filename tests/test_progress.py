"""Tests for shared terminal and JSONL progress reporting."""

from __future__ import annotations

import json
from io import StringIO

import pytest

from business_analysis_agents.progress import (
    ProgressReporter,
    ProgressStatus,
    progress_operation,
    report_progress,
)


def test_progress_reporter_prints_phase_revision_and_writes_jsonl(tmp_path) -> None:
    stream = StringIO()
    log_file = tmp_path / "progress.jsonl"
    reporter = ProgressReporter(log_file=log_file, stream=stream)

    reporter.phase(5, 6, "Cross Consistency", "consistency")
    report_progress(
        reporter,
        phase="consistency",
        step="targeted_revision",
        status=ProgressStatus.REVISION,
        message="Cross revision",
        iteration=1,
        max_iterations=3,
        target="rule",
    )
    with progress_operation(
        reporter,
        phase="consistency",
        step="cross_validation",
        message="Cross re-validation",
        completed_message="Cross validation completed",
    ):
        pass

    output = stream.getvalue()
    assert "[Phase 5/6] Cross Consistency" in output
    assert "[REV] Cross revision 1/3 [target: rule]" in output
    assert "[RUN] Cross re-validation" in output
    assert "[OK] Cross validation completed" in output

    events = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["phase"] == "consistency"
    assert events[1]["iteration"] == 1
    assert events[1]["target"] == "rule"
    assert events[-1]["duration"] is not None
    assert set(events[-1]) == {
        "timestamp",
        "phase",
        "step",
        "status",
        "iteration",
        "max_iterations",
        "duration",
        "message",
        "target",
    }


def test_progress_operation_reports_failure_and_reraises(tmp_path) -> None:
    stream = StringIO()
    reporter = ProgressReporter(
        log_file=tmp_path / "progress.jsonl",
        stream=stream,
    )

    with pytest.raises(RuntimeError, match="API failed"):
        with progress_operation(
            reporter,
            phase="workflow",
            step="rdf_generation",
            message="Workflow RDF generation",
        ):
            raise RuntimeError("API failed")

    assert "[NG] Workflow RDF generation failed" in stream.getvalue()
    events = [
        json.loads(line)
        for line in reporter.log_file.read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["status"] == "failed"
    assert events[-1]["duration"] is not None
