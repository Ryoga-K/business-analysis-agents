"""Interactive CLI helpers for reviewing Consistency findings."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from business_analysis_agents.models import (
    ConsistencyEvaluationResult,
    ConsistencyViolationAnalysis,
    HumanReviewDecision,
    HumanReviewFindingReference,
    HumanReviewFindingResult,
    HumanReviewGroupDecision,
    HumanReviewGroupResult,
    HumanReviewReport,
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

FINDING_DECISION_BY_INPUT = {
    "1": HumanReviewDecision.APPROVE_FINDING,
    "2": HumanReviewDecision.APPROVE_CURRENT_RDF,
    "3": HumanReviewDecision.PROVIDE_CONTEXT,
    "4": HumanReviewDecision.PENDING,
}
GROUP_DECISION_BY_INPUT = {
    "1": HumanReviewGroupDecision.APPROVE_ALL_FINDINGS,
    "2": HumanReviewGroupDecision.APPROVE_ALL_CURRENT_RDF,
    "3": HumanReviewGroupDecision.PROVIDE_GROUP_CONTEXT,
    "4": HumanReviewGroupDecision.REVIEW_INDIVIDUALLY,
    "5": HumanReviewGroupDecision.PENDING,
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


def _request_decision(
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> tuple[HumanReviewDecision, str | None]:
    output_func("1. 指摘を承認")
    output_func("2. 現在のRDFを承認")
    output_func("3. 補足情報を入力")
    output_func("4. 保留")
    while True:
        selected = _read_cli_input(input_func, "判断を選択してください [1-4]: ")
        decision = FINDING_DECISION_BY_INPUT.get(selected)
        if decision is not None:
            break
        output_func("1から4の数字を入力してください。")

    if decision is not HumanReviewDecision.PROVIDE_CONTEXT:
        return decision, None

    return decision, _request_comment(
        input_func,
        output_func,
        "補足情報を入力してください: ",
    )


def _request_group_decision(
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> tuple[HumanReviewGroupDecision, str | None]:
    output_func("1. グループ内の指摘をすべて承認")
    output_func("2. グループ内の現在のRDFをすべて承認")
    output_func("3. グループ全体に補足情報を入力")
    output_func("4. 個別に確認")
    output_func("5. 保留")
    while True:
        selected = _read_cli_input(input_func, "判断を選択してください [1-5]: ")
        decision = GROUP_DECISION_BY_INPUT.get(selected)
        if decision is not None:
            break
        output_func("1から5の数字を入力してください。")

    if decision is not HumanReviewGroupDecision.PROVIDE_GROUP_CONTEXT:
        return decision, None
    return decision, _request_comment(
        input_func,
        output_func,
        "グループ全体の補足情報を入力してください: ",
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


def _display_finding(
    reference: HumanReviewFindingReference,
    display_index: int,
    total: int,
    output_func: Callable[[str], None],
) -> None:
    violation = reference.source_violation
    analysis = reference.source_analysis
    output_func("")
    output_func(f"[{display_index}/{total}] {reference.finding_id}")
    output_func(f"違反内容: {violation.message}")
    output_func(f"対象リソース: {analysis.target_resource}")
    output_func(f"原因: {analysis.cause}")
    output_func(f"修正対象Agent: {analysis.target_agent.value}")
    output_func(f"修正指示: {analysis.repair_instruction}")


def _bulk_finding_decision(
    decision: HumanReviewGroupDecision,
) -> HumanReviewDecision:
    mapping = {
        HumanReviewGroupDecision.APPROVE_ALL_FINDINGS: (
            HumanReviewDecision.APPROVE_FINDING
        ),
        HumanReviewGroupDecision.APPROVE_ALL_CURRENT_RDF: (
            HumanReviewDecision.APPROVE_CURRENT_RDF
        ),
        HumanReviewGroupDecision.PROVIDE_GROUP_CONTEXT: (
            HumanReviewDecision.PROVIDE_CONTEXT
        ),
        HumanReviewGroupDecision.PENDING: HumanReviewDecision.PENDING,
    }
    return mapping[decision]


def _overall_status(
    findings: list[HumanReviewFindingResult],
) -> ReviewStatus:
    decisions = {finding.decision for finding in findings}
    if not decisions or decisions == {HumanReviewDecision.APPROVE_CURRENT_RDF}:
        return ReviewStatus.APPROVED
    if HumanReviewDecision.PENDING in decisions:
        return ReviewStatus.UNKNOWN
    return ReviewStatus.NEEDS_REVISION


def run_human_review(
    workflow_file: Path | str = DEFAULT_WORKFLOW_RDF,
    data_file: Path | str = DEFAULT_DATA_RDF,
    rule_file: Path | str = DEFAULT_RULE_RDF,
    consistency_evaluation_file: Path | str = DEFAULT_CONSISTENCY_EVALUATION,
    output_file: Path | str = DEFAULT_HUMAN_REVIEW_OUTPUT,
    reviewer: str | None = None,
    input_func: Callable[[str], str] | None = None,
    output_func: Callable[[str], None] | None = None,
) -> HumanReviewReport:
    """Collect one human decision for every Consistency finding and save it."""

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

    if not grouped_findings:
        write_output("確認事項なし")
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
        write_output(f"違反タイプ: {violation_type}")
        write_output(f"修正対象Agent: {first_analysis.target_agent.value}")
        write_output(f"該当件数: {len(group)}件")
        write_output(f"共通原因: {common_cause}")
        write_output(f"共通修正方針: {common_repair_policy}")
        write_output("対象例:")
        for resource in target_resources[:5]:
            write_output(f"- {resource}")
        if len(target_resources) > 5:
            write_output(f"- ... 他{len(target_resources) - 5}件")

        group_decision, group_comment = _request_group_decision(
            read_input,
            write_output,
        )
        individual_results: list[HumanReviewFindingResult] = []
        if group_decision is HumanReviewGroupDecision.REVIEW_INDIVIDUALLY:
            for finding_index, reference in enumerate(references, start=1):
                _display_finding(
                    reference,
                    finding_index,
                    len(references),
                    write_output,
                )
                decision, comment = _request_decision(read_input, write_output)
                result = _finding_result(reference, decision, comment)
                individual_results.append(result)
                findings.append(result)
        else:
            finding_decision = _bulk_finding_decision(group_decision)
            findings.extend(
                _finding_result(reference, finding_decision, group_comment)
                for reference in references
            )

        group_results.append(
            HumanReviewGroupResult(
                group_id=group_id,
                group_key=group_key,
                finding_ids=[reference.finding_id for reference in references],
                violation_type=violation_type,
                target_agent=first_analysis.target_agent,
                result_path=first_violation.path,
                constraint_component=first_violation.constraint_component,
                source_shape=first_violation.source_shape,
                common_cause=common_cause,
                common_repair_policy=common_repair_policy,
                target_resources=target_resources,
                decision=group_decision,
                supplemental_comment=group_comment,
                individually_reviewed=(
                    group_decision
                    is HumanReviewGroupDecision.REVIEW_INDIVIDUALLY
                ),
                source_findings=references,
                individual_results=individual_results,
            )
        )

    status = _overall_status(findings)
    summary = (
        "確認事項なし"
        if not findings
        else (
            f"{len(group_results)}グループ、{len(findings)}件の"
            "Consistency findingをレビューしました。"
        )
    )
    report = HumanReviewReport(
        status=status,
        reviewer=reviewer,
        consistency_status=evaluation.status,
        consistency_conforms=evaluation.conforms,
        workflow_rdf_file=str(workflow_path),
        data_rdf_file=str(data_path),
        rule_rdf_file=str(rule_path),
        consistency_evaluation_file=str(evaluation_path),
        groups=group_results,
        findings=findings,
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
