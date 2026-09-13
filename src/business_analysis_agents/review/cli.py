"""Interactive CLI helpers for reviewing Consistency findings."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from business_analysis_agents.models import (
    AgentName,
    ConsistencyEvaluationResult,
    ConsistencyViolationAnalysis,
    HumanReviewDecision,
    HumanReviewDecisionRecord,
    HumanReviewFinalDecision,
    HumanReviewFindingReference,
    HumanReviewFindingResult,
    HumanReviewGroupDecision,
    HumanReviewGroupResult,
    HumanReviewReport,
    HumanReviewRevisionRequest,
    ReviewStatus,
    ShaclViolation,
)


DEFAULT_WORKFLOW_RDF = Path("outputs/workflow/workflow_final.ttl")
DEFAULT_DATA_RDF = Path("outputs/data_rule/data_final.ttl")
DEFAULT_RULE_RDF = Path("outputs/data_rule/rule_final.ttl")
DEFAULT_CONSISTENCY_EVALUATION = Path(
    "outputs/consistency/consistency_evaluation.json"
)
DEFAULT_HUMAN_REVIEW_OUTPUT = Path("outputs/human_review/human_review.json")

FINAL_DECISION_BY_INPUT = {
    "1": HumanReviewFinalDecision.APPROVE,
    "2": HumanReviewFinalDecision.REQUEST_REVISION,
}
TARGET_AGENT_BY_INPUT = {
    "1": AgentName.WORKFLOW,
    "2": AgentName.DATA,
    "3": AgentName.RULE,
}

FindingPair = tuple[int, ShaclViolation, ConsistencyViolationAnalysis]


class HumanReviewInputError(RuntimeError):
    """Raised when interactive review input is unavailable or cancelled."""


def _load_required_text(path: Path | str, label: str) -> tuple[Path, str]:
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Required {label} was not found: {source_path}")
    text = source_path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"Required {label} is empty: {source_path}")
    return source_path.resolve(), text


def _load_consistency_evaluation(
    path: Path | str,
) -> tuple[Path, ConsistencyEvaluationResult]:
    evaluation_path, text = _load_required_text(path, "Consistency evaluation")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid Consistency evaluation JSON: {evaluation_path}: {error}"
        ) from error
    return evaluation_path, ConsistencyEvaluationResult.model_validate(payload)


def _validated_finding_pairs(
    evaluation: ConsistencyEvaluationResult,
) -> list[FindingPair]:
    violations = evaluation.violations
    analyses_by_index = {
        analysis.violation_index: analysis
        for analysis in evaluation.violation_analyses
    }
    if len(analyses_by_index) != len(evaluation.violation_analyses):
        raise ValueError("Consistency evaluation contains duplicate violation indices.")
    expected_indices = set(range(len(violations)))
    if set(analyses_by_index) != expected_indices:
        raise ValueError(
            "Consistency evaluation must contain exactly one analysis per violation. "
            f"expected={sorted(expected_indices)}, "
            f"actual={sorted(analyses_by_index)}"
        )
    return [
        (index, violation, analyses_by_index[index])
        for index, violation in enumerate(violations)
    ]


def _group_key(pair: FindingPair) -> tuple[str, str, str, str, str]:
    _, violation, analysis = pair
    return (
        analysis.target_agent.value,
        violation.path or "",
        violation.constraint_component or "",
        violation.source_shape or "",
        violation.severity.value,
    )


def group_consistency_findings(finding_pairs: list[FindingPair]) -> list[list[FindingPair]]:
    """Group findings deterministically by SHACL structure and repair target."""

    grouped: dict[tuple[str, str, str, str, str], list[FindingPair]] = {}
    for pair in finding_pairs:
        grouped.setdefault(_group_key(pair), []).append(pair)
    return list(grouped.values())


def _read_cli_input(input_func: Callable[[str], str], prompt: str) -> str:
    try:
        return input_func(prompt).strip()
    except (EOFError, KeyboardInterrupt) as error:
        raise HumanReviewInputError(
            "Human Review input was cancelled or is unavailable."
        ) from error


def _request_comment(
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    prompt: str,
) -> str:
    while True:
        comment = _read_cli_input(input_func, prompt)
        if comment:
            return comment
        output_func("補足情報は空にできません。")


def _request_final_decision(
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    *,
    has_findings: bool,
) -> HumanReviewFinalDecision:
    if has_findings:
        output_func("1. 現在のRDFを承認")
        output_func("2. 要修正")
    else:
        output_func("最終成果物として承認しますか？")
        output_func("1. 承認")
        output_func("2. 要修正")
    while True:
        selected = _read_cli_input(input_func, "判断を選択してください [1-2]: ")
        decision = FINAL_DECISION_BY_INPUT.get(selected)
        if decision is not None:
            return decision
        output_func("1または2を入力してください。")


def _request_manual_revision(
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> HumanReviewRevisionRequest:
    output_func("修正対象Agentを選択してください。")
    output_func("1. workflow")
    output_func("2. data")
    output_func("3. rule")
    while True:
        selected = _read_cli_input(input_func, "修正対象を選択してください [1-3]: ")
        target = TARGET_AGENT_BY_INPUT.get(selected)
        if target is not None:
            break
        output_func("1から3の数字を入力してください。")
    instruction = _request_comment(
        input_func,
        output_func,
        "修正内容を入力してください: ",
    )
    return HumanReviewRevisionRequest(
        target_agent=target,
        revision_instruction=instruction,
    )


def _uri_local_name(value: str | None) -> str:
    if not value:
        return "未指定"
    return value.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _violation_type(violation: ShaclViolation) -> str:
    component = _uri_local_name(violation.constraint_component)
    labels = {
        "ClassConstraintComponent": "要求クラス不整合",
        "DatatypeConstraintComponent": "データ型不整合",
        "NodeKindConstraintComponent": "ノード種別不整合",
        "MinCountConstraintComponent": "必須値不足",
        "MaxCountConstraintComponent": "値数上限違反",
        "MinInclusiveConstraintComponent": "最小値違反",
        "MaxInclusiveConstraintComponent": "最大値違反",
        "PatternConstraintComponent": "文字列パターン違反",
        "SPARQLConstraintComponent": "RDF間条件違反",
    }
    violation_label = labels.get(component, component if component != "未指定" else "SHACL制約違反")
    if violation.path:
        return f"{_uri_local_name(violation.path)}: {violation_label}"
    return violation_label


def _finding_reference(pair: FindingPair) -> HumanReviewFindingReference:
    violation_index, violation, analysis = pair
    return HumanReviewFindingReference(
        finding_id=f"consistency-finding-{violation_index:04d}",
        consistency_violation_index=violation_index,
        target_resource=analysis.target_resource,
        source_violation=violation,
        source_analysis=analysis,
    )


def _finding_result(
    reference: HumanReviewFindingReference,
    decision: HumanReviewDecision,
    comment: str | None,
) -> HumanReviewFindingResult:
    return HumanReviewFindingResult(
        **reference.model_dump(),
        decision=decision,
        supplemental_comment=comment,
    )


def _common_text(
    values: list[str],
    structural_description: str,
    representative_label: str,
) -> str:
    if len(set(values)) == 1:
        return values[0]
    return (
        f"{structural_description} 個別内容は元findingを参照してください。"
        f"{representative_label}: {values[0]}"
    )


def run_human_review(
    workflow_file: Path | str = DEFAULT_WORKFLOW_RDF,
    data_file: Path | str = DEFAULT_DATA_RDF,
    rule_file: Path | str = DEFAULT_RULE_RDF,
    consistency_evaluation_file: Path | str = DEFAULT_CONSISTENCY_EVALUATION,
    output_file: Path | str = DEFAULT_HUMAN_REVIEW_OUTPUT,
    reviewer: str | None = None,
    review_round: int = 1,
    decision_history: list[HumanReviewDecisionRecord] | None = None,
    input_func: Callable[[str], str] | None = None,
    output_func: Callable[[str], None] | None = None,
) -> HumanReviewReport:
    """Require an explicit final human decision for the current Cross result."""

    read_input = input_func or input
    write_output = output_func or print
    destination = Path(output_file)
    source_candidates = {
        Path(workflow_file).resolve(),
        Path(data_file).resolve(),
        Path(rule_file).resolve(),
        Path(consistency_evaluation_file).resolve(),
    }
    if destination.resolve() in source_candidates:
        raise ValueError("Human Review output must not overwrite an input file.")
    destination.unlink(missing_ok=True)
    workflow_path, _ = _load_required_text(workflow_file, "Workflow RDF")
    data_path, _ = _load_required_text(data_file, "Data RDF")
    rule_path, _ = _load_required_text(rule_file, "Rule RDF")
    evaluation_path, evaluation = _load_consistency_evaluation(
        consistency_evaluation_file
    )
    finding_pairs = _validated_finding_pairs(evaluation)
    grouped_findings = group_consistency_findings(finding_pairs)
    group_results: list[HumanReviewGroupResult] = []
    findings: list[HumanReviewFindingResult] = []
    group_contexts: list[dict[str, object]] = []

    if not grouped_findings:
        write_output("Cross Consistency: passed")
        write_output("不整合: 0件")
    for group_index, group in enumerate(grouped_findings, start=1):
        references = [_finding_reference(pair) for pair in group]
        first_violation = group[0][1]
        first_analysis = group[0][2]
        group_id = f"consistency-group-{group_index:04d}"
        group_key = json.dumps(_group_key(group[0]), ensure_ascii=False)
        violation_type = _violation_type(first_violation)
        target_resources = list(
            dict.fromkeys(reference.target_resource for reference in references)
        )
        structure = (
            f"同一構造制約 path={first_violation.path or 'none'}, "
            "constraint_component="
            f"{first_violation.constraint_component or 'none'} に対する違反です。"
        )
        common_cause = _common_text(
            [reference.source_analysis.cause for reference in references],
            structure,
            "代表原因",
        )
        common_repair_policy = _common_text(
            [
                reference.source_analysis.repair_instruction
                for reference in references
            ],
            structure,
            "代表修正指示",
        )

        write_output("")
        write_output(f"[Group {group_index}/{len(grouped_findings)}] {group_id}")
        write_output(f"問題内容: {first_violation.message}")
        write_output(f"違反タイプ: {violation_type}")
        write_output(f"修正対象Agent: {first_analysis.target_agent.value}")
        write_output(f"該当件数: {len(group)}件")
        write_output(f"共通原因: {common_cause}")
        write_output(f"修正指示: {common_repair_policy}")
        write_output("対象例:")
        for resource in target_resources[:5]:
            write_output(f"- {resource}")
        if len(target_resources) > 5:
            write_output(f"- ... 他{len(target_resources) - 5}件")
        group_contexts.append(
            {
                "group_id": group_id,
                "group_key": group_key,
                "violation_type": violation_type,
                "first_violation": first_violation,
                "first_analysis": first_analysis,
                "common_cause": common_cause,
                "common_repair_policy": common_repair_policy,
                "target_resources": target_resources,
                "references": references,
            }
        )

    write_output("")
    final_decision = _request_final_decision(
        read_input,
        write_output,
        has_findings=bool(grouped_findings),
    )
    approved = final_decision is HumanReviewFinalDecision.APPROVE
    revision_requested = not approved
    revision_requests: list[HumanReviewRevisionRequest] = []

    if revision_requested and grouped_findings:
        requests_by_target: dict[AgentName, dict[str, list[str]]] = {}
        for pair in finding_pairs:
            reference = _finding_reference(pair)
            analysis = reference.source_analysis
            request = requests_by_target.setdefault(
                analysis.target_agent,
                {"instructions": [], "finding_ids": [], "resources": []},
            )
            if analysis.repair_instruction not in request["instructions"]:
                request["instructions"].append(analysis.repair_instruction)
            request["finding_ids"].append(reference.finding_id)
            if reference.target_resource not in request["resources"]:
                request["resources"].append(reference.target_resource)
        revision_requests = [
            HumanReviewRevisionRequest(
                target_agent=target,
                revision_instruction="\n".join(values["instructions"]),
                source_finding_ids=values["finding_ids"],
                target_resources=values["resources"],
            )
            for target, values in requests_by_target.items()
        ]
    elif revision_requested:
        revision_requests = [_request_manual_revision(read_input, write_output)]

    finding_decision = (
        HumanReviewDecision.APPROVE_CURRENT_RDF
        if approved
        else HumanReviewDecision.APPROVE_FINDING
    )
    group_decision = (
        HumanReviewGroupDecision.APPROVE_ALL_CURRENT_RDF
        if approved
        else HumanReviewGroupDecision.APPROVE_ALL_FINDINGS
    )
    for context in group_contexts:
        references = context["references"]
        assert isinstance(references, list)
        findings.extend(
            _finding_result(reference, finding_decision, None)
            for reference in references
        )
        first_violation = context["first_violation"]
        first_analysis = context["first_analysis"]
        assert isinstance(first_violation, ShaclViolation)
        assert isinstance(first_analysis, ConsistencyViolationAnalysis)
        group_results.append(
            HumanReviewGroupResult(
                group_id=str(context["group_id"]),
                group_key=str(context["group_key"]),
                finding_ids=[reference.finding_id for reference in references],
                violation_type=str(context["violation_type"]),
                target_agent=first_analysis.target_agent,
                result_path=first_violation.path,
                constraint_component=first_violation.constraint_component,
                source_shape=first_violation.source_shape,
                common_cause=str(context["common_cause"]),
                common_repair_policy=str(context["common_repair_policy"]),
                target_resources=list(context["target_resources"]),
                decision=group_decision,
                source_findings=references,
            )
        )

    status = ReviewStatus.APPROVED if approved else ReviewStatus.NEEDS_REVISION
    current_record = HumanReviewDecisionRecord(
        review_round=review_round,
        decision=final_decision,
        approved=approved,
        revision_requested=revision_requested,
        revision_requests=revision_requests,
    )
    history = [*(decision_history or []), current_record]
    summary = (
        "人間が最終成果物を承認しました。"
        if approved
        else "人間がRDFの修正を要求しました。"
    )
    report = HumanReviewReport(
        status=status,
        input_received=True,
        final_decision=final_decision,
        approved=approved,
        revision_requested=revision_requested,
        review_round=review_round,
        reviewer=reviewer,
        consistency_status=evaluation.status,
        consistency_conforms=evaluation.conforms,
        workflow_rdf_file=str(workflow_path),
        data_rdf_file=str(data_path),
        rule_rdf_file=str(rule_path),
        consistency_evaluation_file=str(evaluation_path),
        groups=group_results,
        findings=findings,
        revision_requests=revision_requests,
        decision_history=history,
        summary=summary,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def request_human_decision(violation: ShaclViolation) -> str:
    """Backward-compatible helper for the earlier single-violation CLI."""

    print(f"SHACL違反: {violation.message}")
    return input("対応方針を入力してください: ")
