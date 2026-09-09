"""Tests for interactive Human Review of Consistency findings."""

from __future__ import annotations

import json

import pytest

from business_analysis_agents import controller
from business_analysis_agents.models import (
    AgentName,
    ConsistencyEvaluationResult,
    ConsistencyViolationAnalysis,
    HumanReviewDecision,
    HumanReviewGroupDecision,
    RdfKind,
    ReviewStatus,
    ShaclViolation,
)
from business_analysis_agents.review.cli import (
    group_consistency_findings,
    run_human_review,
)


def _write_rdf_inputs(tmp_path):
    paths = []
    for name in ("workflow.ttl", "data.ttl", "rule.ttl"):
        path = tmp_path / name
        path.write_text("<urn:s> <urn:p> <urn:o> .", encoding="utf-8")
        paths.append(path)
    return paths


def _write_evaluation(tmp_path, evaluation):
    path = tmp_path / "consistency_evaluation.json"
    path.write_text(
        json.dumps(evaluation.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_human_review_saves_no_findings_without_prompting(tmp_path) -> None:
    """A conforming Consistency result produces an approved empty review."""

    workflow, data, rule = _write_rdf_inputs(tmp_path)
    evaluation_file = _write_evaluation(
        tmp_path,
        ConsistencyEvaluationResult(
            status=ReviewStatus.APPROVED,
            conforms=True,
            can_auto_repair=False,
            reason="No cross-RDF violations.",
        ),
    )
    messages: list[str] = []

    def must_not_prompt(prompt):
        pytest.fail(f"Unexpected prompt: {prompt}")

    output_file = tmp_path / "human_review.json"
    report = run_human_review(
        workflow_file=workflow,
        data_file=data,
        rule_file=rule,
        consistency_evaluation_file=evaluation_file,
        output_file=output_file,
        input_func=must_not_prompt,
        output_func=messages.append,
    )

    assert report.status is ReviewStatus.APPROVED
    assert report.groups == []
    assert report.findings == []
    assert messages == ["確認事項なし"]
    saved = json.loads(output_file.read_text(encoding="utf-8"))
    assert saved["summary"] == "確認事項なし"
    assert saved["consistency_conforms"] is True


def test_human_review_collects_all_decisions_and_supplemental_context(tmp_path) -> None:
    """All four decisions remain linked to their original Consistency findings."""

    workflow, data, rule = _write_rdf_inputs(tmp_path)
    violations = [
        ShaclViolation(
            focus_node=f"urn:resource:{index}",
            message=f"violation {index}",
            rdf_kind=RdfKind.CONSISTENCY,
        )
        for index in range(4)
    ]
    analyses = [
        ConsistencyViolationAnalysis(
            violation_index=index,
            target_resource=f"urn:resource:{index}",
            cause=f"cause {index}",
            target_agent=AgentName.DATA,
            repair_instruction=f"repair {index}",
        )
        for index in range(4)
    ]
    evaluation_file = _write_evaluation(
        tmp_path,
        ConsistencyEvaluationResult(
            status=ReviewStatus.NEEDS_REVISION,
            conforms=False,
            can_auto_repair=True,
            reason="Four findings.",
            related_violation_indices=list(range(4)),
            violations=violations,
            violation_analyses=analyses,
        ),
    )
    answers = iter(["4", "1", "2", "3", "PDFの別紙を確認する", "4"])
    messages: list[str] = []
    output_file = tmp_path / "human_review.json"

    report = run_human_review(
        workflow_file=workflow,
        data_file=data,
        rule_file=rule,
        consistency_evaluation_file=evaluation_file,
        output_file=output_file,
        reviewer="reviewer-a",
        input_func=lambda prompt: next(answers),
        output_func=messages.append,
    )

    assert report.status is ReviewStatus.UNKNOWN
    assert len(report.groups) == 1
    assert report.groups[0].decision is HumanReviewGroupDecision.REVIEW_INDIVIDUALLY
    assert report.groups[0].individually_reviewed is True
    assert len(report.groups[0].individual_results) == 4
    assert [finding.decision for finding in report.findings] == [
        HumanReviewDecision.APPROVE_FINDING,
        HumanReviewDecision.APPROVE_CURRENT_RDF,
        HumanReviewDecision.PROVIDE_CONTEXT,
        HumanReviewDecision.PENDING,
    ]
    assert report.findings[2].supplemental_comment == "PDFの別紙を確認する"
    assert report.findings[2].consistency_violation_index == 2
    assert report.findings[2].source_analysis.repair_instruction == "repair 2"
    rendered = "\n".join(messages)
    assert "[Group 1/1]" in rendered
    assert "該当件数: 4件" in rendered
    assert "4. 個別に確認" in rendered
    for label in ("違反内容", "対象リソース", "原因", "修正対象Agent", "修正指示"):
        assert label in rendered
    saved = json.loads(output_file.read_text(encoding="utf-8"))
    assert saved["findings"][2]["finding_id"] == "consistency-finding-0002"
    assert saved["findings"][2]["source_violation"]["message"] == "violation 2"
    assert saved["groups"][0]["finding_ids"] == [
        "consistency-finding-0000",
        "consistency-finding-0001",
        "consistency-finding-0002",
        "consistency-finding-0003",
    ]


def test_grouping_uses_structural_fields_instead_of_natural_language() -> None:
    """Different prose stays grouped while a different source shape splits a group."""

    def pair(index, shape, cause):
        return (
            index,
            ShaclViolation(
                focus_node=f"urn:resource:{index}",
                message=f"unrelated prose {index}",
                path="urn:property:reference",
                source_shape=shape,
                constraint_component=(
                    "http://www.w3.org/ns/shacl#ClassConstraintComponent"
                ),
                rdf_kind=RdfKind.CONSISTENCY,
            ),
            ConsistencyViolationAnalysis(
                violation_index=index,
                target_resource=f"urn:resource:{index}",
                cause=cause,
                target_agent=AgentName.RULE,
                repair_instruction=f"repair prose {index}",
            ),
        )

    groups = group_consistency_findings(
        [
            pair(0, "urn:shape:one", "first wording"),
            pair(1, "urn:shape:one", "completely different wording"),
            pair(2, "urn:shape:two", "first wording"),
        ]
    )

    assert [len(group) for group in groups] == [2, 1]


def test_group_review_applies_one_decision_to_all_findings(tmp_path) -> None:
    """A group-level decision avoids individual prompts and remains traceable."""

    workflow, data, rule = _write_rdf_inputs(tmp_path)
    violations = [
        ShaclViolation(
            focus_node=f"urn:resource:{index}",
            message=f"violation {index}",
            path="urn:property:reference",
            source_shape="urn:shape:reference",
            constraint_component=(
                "http://www.w3.org/ns/shacl#ClassConstraintComponent"
            ),
            rdf_kind=RdfKind.CONSISTENCY,
        )
        for index in range(2)
    ]
    analyses = [
        ConsistencyViolationAnalysis(
            violation_index=index,
            target_resource=f"urn:resource:{index}",
            cause=f"resource-specific cause {index}",
            target_agent=AgentName.RULE,
            repair_instruction=f"resource-specific repair {index}",
        )
        for index in range(2)
    ]
    evaluation_file = _write_evaluation(
        tmp_path,
        ConsistencyEvaluationResult(
            status=ReviewStatus.NEEDS_REVISION,
            conforms=False,
            can_auto_repair=True,
            reason="Two findings.",
            violations=violations,
            violation_analyses=analyses,
        ),
    )
    answers = iter(["3", "参照先クラスの根拠を再確認する"])

    report = run_human_review(
        workflow_file=workflow,
        data_file=data,
        rule_file=rule,
        consistency_evaluation_file=evaluation_file,
        output_file=tmp_path / "human_review.json",
        input_func=lambda prompt: next(answers),
        output_func=lambda message: None,
    )

    group = report.groups[0]
    assert group.decision is HumanReviewGroupDecision.PROVIDE_GROUP_CONTEXT
    assert group.individually_reviewed is False
    assert group.individual_results == []
    assert len(group.source_findings) == 2
    assert [finding.decision for finding in report.findings] == [
        HumanReviewDecision.PROVIDE_CONTEXT,
        HumanReviewDecision.PROVIDE_CONTEXT,
    ]
    assert all(
        finding.supplemental_comment == "参照先クラスの根拠を再確認する"
        for finding in report.findings
    )


def test_human_review_rejects_invalid_consistency_json(tmp_path) -> None:
    """Malformed Consistency input is a Human Review processing failure."""

    workflow, data, rule = _write_rdf_inputs(tmp_path)
    evaluation_file = tmp_path / "consistency_evaluation.json"
    evaluation_file.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid Consistency evaluation JSON"):
        run_human_review(
            workflow_file=workflow,
            data_file=data,
            rule_file=rule,
            consistency_evaluation_file=evaluation_file,
            output_file=tmp_path / "human_review.json",
        )


def test_human_review_subcommand_uses_supplied_files(tmp_path, capsys) -> None:
    """The standalone human-review command runs without an OpenAI API call."""

    workflow, data, rule = _write_rdf_inputs(tmp_path)
    evaluation_file = _write_evaluation(
        tmp_path,
        ConsistencyEvaluationResult(
            status=ReviewStatus.APPROVED,
            conforms=True,
            can_auto_repair=False,
            reason="No findings.",
        ),
    )
    output_file = tmp_path / "human_review.json"

    exit_code = controller.run(
        [
            "human-review",
            "--workflow",
            str(workflow),
            "--data",
            str(data),
            "--rule",
            str(rule),
            "--consistency-evaluation",
            str(evaluation_file),
            "--output",
            str(output_file),
            "--reviewer",
            "reviewer-a",
        ]
    )

    assert exit_code == 0
    assert output_file.is_file()
    assert "Human Reviewを完了しました" in capsys.readouterr().out
