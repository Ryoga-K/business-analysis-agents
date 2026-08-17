"""エージェント入出力、検証、修正ループで共有するPydanticモデル。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    """研究用プロトタイプで共通利用する厳格なPydantic基底モデル。"""

    model_config = ConfigDict(extra="forbid")


class RdfKind(str, Enum):
    """生成・検証対象となるRDF種別。"""

    WORKFLOW = "workflow"
    DATA = "data"
    RULE = "rule"


class AgentName(str, Enum):
    """再実行や修正対象として識別するエージェント名。"""

    SCENARIO = "scenario"
    WORKFLOW = "workflow"
    DATA = "data"
    RULE = "rule"
    CONSISTENCY = "consistency"
    HUMAN_REVIEW = "human_review"


class Severity(str, Enum):
    """レビュー結果や検証違反の重大度。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ReviewStatus(str, Enum):
    """人間または自己レビューの判定状態。"""

    APPROVED = "approved"
    NEEDS_REVISION = "needs_revision"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class RepairAction(str, Enum):
    """修正ループで実行する方針。"""

    RERUN_AGENT = "rerun_agent"
    ASK_HUMAN = "ask_human"
    STOP = "stop"


class RunStatus(str, Enum):
    """実行全体の進行状態。"""

    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowAgentMode(str, Enum):
    """Workflow Agent internal processing mode."""

    ONTOLOGY_SETUP = "ontology_setup"
    WORKFLOW_GENERATION = "workflow_generation"
    WORKFLOW_REVISION = "workflow_revision"


class DataRuleAgentMode(str, Enum):
    """Related data and rule extraction agent internal processing mode."""

    ONTOLOGY_SETUP = "ontology_setup"
    DATA_GENERATION = "data_generation"
    DATA_REVISION = "data_revision"
    RULE_GENERATION = "rule_generation"
    RULE_REVISION = "rule_revision"


class SourceDocument(StrictBaseModel):
    """業務文書から抽出したテキストと識別情報。"""

    document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_path: str | None = None
    pages: list["PageText"] = Field(default_factory=list)


class PageText(StrictBaseModel):
    """PDFからページ単位で抽出したテキスト。"""

    page_number: int = Field(ge=1)
    text: str


class EvidenceSpan(StrictBaseModel):
    """抽出結果の根拠となる文書ページと本文抜粋。"""

    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)


class ProcedureRelation(StrictBaseModel):
    """業務手順の前後関係を表す構造。"""

    previous_step_ids: list[str] = Field(default_factory=list)
    next_step_ids: list[str] = Field(default_factory=list)


class BusinessProcedureStep(StrictBaseModel):
    """業務シナリオ内の1つの業務手順。"""

    step_id: str = Field(min_length=1)
    step_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    input_data: list[str] = Field(default_factory=list)
    output_data: list[str] = Field(default_factory=list)
    relation: ProcedureRelation = Field(default_factory=ProcedureRelation)
    branch_conditions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    is_uncertain: bool = False


class BusinessScenario(StrictBaseModel):
    """固定フォーマットで表現する業務シナリオ。"""

    scenario_id: str = Field(min_length=1)
    business_name: str = Field(min_length=1)
    participants: list[str] = Field(default_factory=list)
    business_goal: str = Field(min_length=1)
    business_overview: str = Field(min_length=1)
    procedure_steps: list[BusinessProcedureStep] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)


class ScenarioAgentInput(StrictBaseModel):
    """シナリオ作成エージェントに渡す入力。"""

    document: SourceDocument


class ScenarioAgentOutput(StrictBaseModel):
    """シナリオ作成エージェントから返す構造化出力。"""

    scenarios: list[BusinessScenario] = Field(default_factory=list)


class WorkflowExtractionInput(StrictBaseModel):
    """Workflow抽出エージェントに渡す入力。"""

    document: SourceDocument
    scenarios: list[BusinessScenario] = Field(default_factory=list)


class WorkflowStep(StrictBaseModel):
    """Workflow RDFへ変換する前の構造化された業務フロー手順。"""

    step_id: str = Field(min_length=1)
    step_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    input_data: list[str] = Field(default_factory=list)
    output_data: list[str] = Field(default_factory=list)
    previous_step_ids: list[str] = Field(default_factory=list)
    next_step_ids: list[str] = Field(default_factory=list)
    branch_conditions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    is_uncertain: bool = False


class WorkflowExtractionOutput(StrictBaseModel):
    """Workflow抽出結果。"""

    scenario_id: str = Field(min_length=1)
    steps: list[WorkflowStep] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)


class WorkflowAgentOutput(StrictBaseModel):
    """Single structured output model used by all Workflow Agent modes.

    The Turtle fields required by each mode are validated here. Explanatory
    and design-oriented fields are intentionally flexible while the prototype
    is still evolving; RDF syntax, vocabulary, and SHACL validity are checked
    later by RDFLib and pySHACL.
    """

    mode: WorkflowAgentMode
    ontology_turtle: str | None = None
    shacl_turtle: str | None = None
    reused_standard_terms: Any = Field(default_factory=list)
    provisional_classes: Any = Field(default_factory=list)
    provisional_properties: Any = Field(default_factory=list)
    class_property_mapping: dict[str, Any] = Field(default_factory=dict)
    ontology_design_notes: Any = Field(default_factory=list)
    shacl_design_notes: Any = Field(default_factory=list)
    unresolved_design_issues: Any = Field(default_factory=list)
    ontology_version: str | None = None
    namespace_uri: str | None = None
    workflow_rdf_turtle: str | None = None
    used_vocabulary_terms: Any = Field(default_factory=list)
    unresolved_items: Any = Field(default_factory=list)
    generation_notes: Any = Field(default_factory=list)
    evidence_summary: Any = Field(default_factory=list)
    revision_summary: str | None = None
    addressed_violations: Any = Field(default_factory=list)
    remaining_violations: Any = Field(default_factory=list)


class DataRuleAgentOutput(StrictBaseModel):
    """Single structured output model used by related data/rule extraction modes.

    Only the Turtle text required by each mode is strict. Explanatory fields are
    flexible JSON so the prototype can absorb early LLM output variance while
    RDFLib and pySHACL validate the actual RDF content.
    """

    mode: DataRuleAgentMode
    ontology_turtle: str | None = None
    data_shacl_turtle: str | None = None
    rule_shacl_turtle: str | None = None
    data_rdf_turtle: str | None = None
    rule_rdf_turtle: str | None = None
    reused_standard_terms: Any = Field(default_factory=list)
    provisional_classes: Any = Field(default_factory=list)
    provisional_properties: Any = Field(default_factory=list)
    class_property_mapping: dict[str, Any] = Field(default_factory=dict)
    ontology_design_notes: Any = Field(default_factory=list)
    data_shacl_design_notes: Any = Field(default_factory=list)
    rule_shacl_design_notes: Any = Field(default_factory=list)
    unresolved_design_issues: Any = Field(default_factory=list)
    ontology_version: str | None = None
    namespace_uri: str | None = None
    used_vocabulary_terms: Any = Field(default_factory=list)
    unresolved_items: Any = Field(default_factory=list)
    generation_notes: Any = Field(default_factory=list)
    evidence_summary: Any = Field(default_factory=list)
    revision_summary: str | None = None
    addressed_violations: Any = Field(default_factory=list)
    remaining_violations: Any = Field(default_factory=list)



class RdfParseError(StrictBaseModel):
    """RDFLib parse error details."""

    error_type: str
    error_message: str
    line: int | None = None
    column: int | None = None
    iteration: int = 0


class VocabularyValidationResult(StrictBaseModel):
    """Validation result for unauthorized RDF vocabulary terms."""

    vocabulary_conforms: bool
    unauthorized_term_count: int = 0
    unauthorized_terms: list[str] = Field(default_factory=list)


class WorkflowRdfValidationResult(StrictBaseModel):
    """Combined validation result for Workflow RDF."""

    structure_conforms: bool
    shacl_conforms: bool
    vocabulary: VocabularyValidationResult = Field(
        default_factory=lambda: VocabularyValidationResult(vocabulary_conforms=True)
    )
    parse_errors: list[RdfParseError] = Field(default_factory=list)
    shacl_result: ShaclValidationResult | None = None

    @property
    def conforms(self) -> bool:
        """Return True only when all validation layers pass."""

        return (
            self.structure_conforms
            and self.shacl_conforms
            and self.vocabulary.vocabulary_conforms
        )


class OntologyValidationResult(StrictBaseModel):
    """Validation result for generated ontology and SHACL shapes."""

    ontology_turtle_valid: bool
    shacl_turtle_valid: bool
    prefixes_defined: bool
    domain_range_references_defined: bool
    shapes_references_defined_terms: bool
    no_duplicate_uri_definitions: bool
    ontology_hash: str | None = None
    shapes_hash: str | None = None
    parse_errors: list[RdfParseError] = Field(default_factory=list)
    undefined_references: list[str] = Field(default_factory=list)

    @property
    def conforms(self) -> bool:
        """Return True when ontology and shapes are fixed for the run."""

        return (
            self.ontology_turtle_valid
            and self.shacl_turtle_valid
            and self.prefixes_defined
            and self.domain_range_references_defined
            and self.shapes_references_defined_terms
            and self.no_duplicate_uri_definitions
        )


class DataExtractionInput(StrictBaseModel):
    """Data抽出エージェントに渡す入力。"""

    document: SourceDocument
    workflow_steps: list[WorkflowStep] = Field(default_factory=list)


class DataAttribute(StrictBaseModel):
    """業務データ項目の属性。"""

    name: str = Field(min_length=1)
    description: str | None = None
    data_type: str | None = None
    required: bool | None = None


class DataEntity(StrictBaseModel):
    """Data RDFへ変換する前の構造化された業務データ。"""

    entity_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    attributes: list[DataAttribute] = Field(default_factory=list)
    related_step_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    is_uncertain: bool = False


class DataExtractionOutput(StrictBaseModel):
    """Data抽出結果。"""

    entities: list[DataEntity] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)


class RuleExtractionInput(StrictBaseModel):
    """Rule抽出エージェントに渡す入力。"""

    document: SourceDocument
    workflow_steps: list[WorkflowStep] = Field(default_factory=list)
    data_entities: list[DataEntity] = Field(default_factory=list)


class BusinessRule(StrictBaseModel):
    """Rule RDFへ変換する前の構造化された業務ルール。"""

    rule_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    condition: str | None = None
    action: str | None = None
    related_step_ids: list[str] = Field(default_factory=list)
    related_entity_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    is_uncertain: bool = False


class RuleExtractionOutput(StrictBaseModel):
    """Rule抽出結果。"""

    rules: list[BusinessRule] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)


class DataRuleExtractionInput(StrictBaseModel):
    """Data・Rule抽出を同時に行う場合の入力。"""

    document: SourceDocument
    workflow_steps: list[WorkflowStep] = Field(default_factory=list)


class DataRuleExtractionOutput(StrictBaseModel):
    """Data抽出結果とRule抽出結果をまとめた出力。"""

    data: DataExtractionOutput = Field(default_factory=DataExtractionOutput)
    rule: RuleExtractionOutput = Field(default_factory=RuleExtractionOutput)

    @property
    def data_entities(self) -> list[DataEntity]:
        """既存コードとの互換用にDataエンティティ一覧を返す。"""

        return self.data.entities

    @property
    def rules(self) -> list[BusinessRule]:
        """既存コードとの互換用にRule一覧を返す。"""

        return self.rule.rules


class SelfReviewFinding(StrictBaseModel):
    """自己レビューで見つかった個別の指摘。"""

    severity: Severity
    message: str = Field(min_length=1)
    target_id: str | None = None
    suggestion: str | None = None


class SelfReviewResult(StrictBaseModel):
    """抽出エージェント自身による出力品質レビュー結果。"""

    reviewer_agent: AgentName
    status: ReviewStatus
    findings: list[SelfReviewFinding] = Field(default_factory=list)
    summary: str | None = None


class ShaclViolation(StrictBaseModel):
    """pySHACLの検証レポートから抽出したSHACL違反。"""

    focus_node: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: str | None = None
    source_shape: str | None = None
    severity: Severity = Severity.ERROR
    rdf_kind: RdfKind | None = None


class ShaclValidationResult(StrictBaseModel):
    """pySHACLによる適合判定結果。"""

    conforms: bool
    violations: list[ShaclViolation] = Field(default_factory=list)
    report_text: str | None = None


class ConsistencyEvaluationInput(StrictBaseModel):
    """整合性評価エージェントに渡す入力。"""

    validation_result: ShaclValidationResult
    workflow: WorkflowExtractionOutput
    data: DataExtractionOutput
    rule: RuleExtractionOutput


class ConsistencyEvaluationResult(StrictBaseModel):
    """RDF間の不整合に対する評価結果。"""

    status: ReviewStatus
    can_auto_repair: bool
    target_agent: AgentName | None = None
    reason: str = Field(min_length=1)
    related_violation_indices: list[int] = Field(default_factory=list)


class ConsistencyEvaluationOutput(ConsistencyEvaluationResult):
    """既存コードとの互換用の整合性評価出力。"""


class RepairInstruction(StrictBaseModel):
    """修正ループで次に実行すべき処理を表す指示。"""

    action: RepairAction
    target_agent: AgentName | None = None
    reason: str = Field(min_length=1)
    target_ids: list[str] = Field(default_factory=list)
    human_prompt: str | None = None


class HumanReviewResult(StrictBaseModel):
    """CLIで人間が確認した結果。"""

    status: ReviewStatus
    reviewer: str | None = None
    comment: str = Field(min_length=1)
    selected_action: RepairAction | None = None
    corrected_text: str | None = None


class RepairHistoryEntry(StrictBaseModel):
    """1回分の修正試行の履歴。"""

    iteration: int = Field(ge=0)
    instruction: RepairInstruction
    result_status: ReviewStatus
    message: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class RepairHistory(StrictBaseModel):
    """修正ループ全体の履歴。"""

    entries: list[RepairHistoryEntry] = Field(default_factory=list)


class RunState(StrictBaseModel):
    """文書処理1回分の実行全体の状態。"""

    run_id: str = Field(min_length=1)
    status: RunStatus
    output_dir: str = Field(min_length=1)
    document: SourceDocument | None = None
    scenario_output: ScenarioAgentOutput | None = None
    workflow_output: WorkflowExtractionOutput | None = None
    data_output: DataExtractionOutput | None = None
    rule_output: RuleExtractionOutput | None = None
    shacl_result: ShaclValidationResult | None = None
    consistency_result: ConsistencyEvaluationResult | None = None
    repair_history: RepairHistory = Field(default_factory=RepairHistory)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
