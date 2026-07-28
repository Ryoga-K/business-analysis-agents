"""Pydanticモデルの正常系とバリデーションエラーのテスト。"""

import pytest
from pydantic import ValidationError

from business_analysis_agents.models import (
    AgentName,
    BusinessProcedureStep,
    BusinessRule,
    BusinessScenario,
    ConsistencyEvaluationResult,
    DataAttribute,
    DataEntity,
    DataExtractionOutput,
    EvidenceSpan,
    HumanReviewResult,
    RepairAction,
    RepairHistory,
    RepairHistoryEntry,
    RepairInstruction,
    ReviewStatus,
    RuleExtractionOutput,
    RunState,
    RunStatus,
    ScenarioAgentOutput,
    Severity,
    ShaclValidationResult,
    ShaclViolation,
    SourceDocument,
    WorkflowExtractionOutput,
    WorkflowStep,
)


def test_models_can_be_serialized_to_json() -> None:
    """主要なモデルを組み立て、JSONへシリアライズできる。"""

    document = SourceDocument(
        document_id="doc-001",
        title="受注業務マニュアル",
        text="営業担当者は注文書を確認し、在庫を確認する。",
    )
    procedure_step = BusinessProcedureStep(
        step_id="S1",
        step_name="注文確認",
        description="注文書の内容を確認する。",
        actor="営業担当者",
        input_data=["注文書"],
        output_data=["確認済み注文"],
        branch_conditions=["在庫が不足している場合は購買へ連絡する。"],
        evidence=[EvidenceSpan(page_number=1, text="営業担当者は注文書を確認する。")],
    )
    scenario = BusinessScenario(
        scenario_id="SCN1",
        business_name="受注業務",
        participants=["営業担当者", "倉庫担当者"],
        business_goal="注文を正確に処理する。",
        business_overview="注文受付から在庫確認までを扱う。",
        procedure_steps=[procedure_step],
        open_issues=["例外処理の詳細が未確定。"],
    )
    workflow = WorkflowExtractionOutput(
        scenario_id="SCN1",
        steps=[
            WorkflowStep(
                step_id="S1",
                step_name="注文確認",
                description="注文書の内容を確認する。",
                actor="営業担当者",
                input_data=["注文書"],
                output_data=["確認済み注文"],
                evidence=[
                    EvidenceSpan(page_number=1, text="営業担当者は注文書を確認する。")
                ],
            )
        ],
    )
    data = DataExtractionOutput(
        entities=[
            DataEntity(
                entity_id="D1",
                name="注文書",
                attributes=[DataAttribute(name="注文番号", data_type="string")],
                related_step_ids=["S1"],
            )
        ]
    )
    rule = RuleExtractionOutput(
        rules=[
            BusinessRule(
                rule_id="R1",
                name="在庫不足時の連絡",
                description="在庫不足時は購買へ連絡する。",
                condition="在庫が不足している。",
                action="購買へ連絡する。",
                related_step_ids=["S1"],
                related_entity_ids=["D1"],
            )
        ]
    )
    shacl = ShaclValidationResult(
        conforms=False,
        violations=[
            ShaclViolation(
                focus_node="ex:S1",
                message="出力データがData RDFに存在しません。",
                severity=Severity.ERROR,
            )
        ],
    )
    consistency = ConsistencyEvaluationResult(
        status=ReviewStatus.NEEDS_REVISION,
        can_auto_repair=True,
        target_agent=AgentName.DATA,
        reason="Workflowの出力データに対応するDataエンティティが不足している。",
        related_violation_indices=[0],
    )
    instruction = RepairInstruction(
        action=RepairAction.RERUN_AGENT,
        target_agent=AgentName.DATA,
        reason="Data抽出エージェントを再実行する。",
        target_ids=["D1"],
    )
    history = RepairHistory(
        entries=[
            RepairHistoryEntry(
                iteration=1,
                instruction=instruction,
                result_status=ReviewStatus.NEEDS_REVISION,
            )
        ]
    )
    state = RunState(
        run_id="run-001",
        status=RunStatus.RUNNING,
        output_dir="outputs/run_20260728_142800",
        document=document,
        scenario_output=ScenarioAgentOutput(scenarios=[scenario]),
        workflow_output=workflow,
        data_output=data,
        rule_output=rule,
        shacl_result=shacl,
        consistency_result=consistency,
        repair_history=history,
    )

    json_text = state.model_dump_json()

    assert "受注業務" in json_text
    assert "run-001" in json_text


def test_required_fields_are_validated() -> None:
    """必須項目が欠落した場合はValidationErrorになる。"""

    with pytest.raises(ValidationError):
        BusinessProcedureStep(
            step_id="S1",
            step_name="注文確認",
            description="注文書の内容を確認する。",
            input_data=["注文書"],
            output_data=["確認済み注文"],
        )


def test_invalid_enum_value_is_rejected() -> None:
    """Enumに存在しない値はValidationErrorになる。"""

    with pytest.raises(ValidationError):
        HumanReviewResult(
            status="done",
            comment="この値はReviewStatusに存在しない。",
            selected_action=RepairAction.STOP,
        )
