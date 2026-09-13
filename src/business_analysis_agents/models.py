"""エージェント入出力、検証、修正ループで共有するPydanticモデル。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictBaseModel(BaseModel):
    """研究用プロトタイプで共通利用する厳格なPydantic基底モデル。"""

    model_config = ConfigDict(extra="forbid")


class RdfKind(str, Enum):
    """生成・検証対象となるRDF種別。"""

    WORKFLOW = "workflow"
    DATA = "data"
    RULE = "rule"
    CONSISTENCY = "consistency"


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


class HumanReviewDecision(str, Enum):
    """Consistency findingに対する人間の判断。"""

    APPROVE_FINDING = "approve_finding"
    APPROVE_CURRENT_RDF = "approve_current_rdf"
    PROVIDE_CONTEXT = "provide_context"
    PENDING = "pending"


class HumanReviewGroupDecision(str, Enum):
    """Consistency findingグループに対する人間の判断。"""

    APPROVE_ALL_FINDINGS = "approve_all_findings"
    APPROVE_ALL_CURRENT_RDF = "approve_all_current_rdf"
    PROVIDE_GROUP_CONTEXT = "provide_group_context"
    REVIEW_INDIVIDUALLY = "review_individually"
    PENDING = "pending"


class HumanReviewFinalDecision(str, Enum):
    """人間がE2E成果物に対して明示した最終判断。"""

    APPROVE = "approve"
    REQUEST_REVISION = "request_revision"


class RunStatus(str, Enum):
    """実行全体の進行状態。"""

    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    FATAL_FAILED = "fatal_failed"
    FAILED = "failed"


class ControllerStage(str, Enum):
    """End-to-End Controllerが順番に実行する工程。"""

    SCENARIO = "scenario"
    WORKFLOW = "workflow"
    DATA = "data"
    DATA_RULE = "data_rule"
    CONSISTENCY = "consistency"
    HUMAN_REVIEW = "human_review"


class ControllerStageStatus(str, Enum):
    """Controller内の各工程の実行状態。"""

    PENDING = "pending"
    COMPLETED = "completed"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    NEEDS_REVIEW = "needs_review"
    FATAL_FAILED = "fatal_failed"
    FAILED = "failed"
    SKIPPED = "skipped"


class FinalizationStatus(str, Enum):
    """Human Review後を含むE2E最終成果物の確定状態。"""

    COMPLETED_AFTER_HUMAN_APPROVAL = "completed_after_human_approval"
    COMPLETED_AFTER_HUMAN_REVISION = "completed_after_human_revision"
    UNRESOLVED_AFTER_HUMAN_REVIEW = "unresolved_after_human_review"
    PIPELINE_FAILED = "pipeline_failed"


class ControllerStageResult(StrictBaseModel):
    """Controllerが記録する1工程分の実行結果。"""

    stage: ControllerStage
    status: ControllerStageStatus = ControllerStageStatus.PENDING
    pipeline_status: str | None = None
    output_files: dict[str, str] = Field(default_factory=dict)
    unresolved_shacl_violation_count: int = Field(default=0, ge=0)
    unresolved_self_review_finding_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None


class IndividualRdfIssueSummary(StrictBaseModel):
    """Unresolved individual validation details for one usable RDF."""

    rdf_kind: RdfKind
    rdf_file: str = Field(min_length=1)
    usable: bool
    validation_conforms: bool
    shacl_conforms: bool
    shacl_violation_count: int = Field(default=0, ge=0)
    shacl_violations: list[dict[str, Any]] = Field(default_factory=list)
    vocabulary_violation_count: int = Field(default=0, ge=0)
    unauthorized_terms: list[str] = Field(default_factory=list)
    self_review_status: str
    self_review_finding_count: int = Field(default=0, ge=0)
    self_review_findings: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return (
            not self.validation_conforms
            or self.self_review_status != "passed"
            or self.shacl_violation_count > 0
            or self.self_review_finding_count > 0
            or self.vocabulary_violation_count > 0
        )


class ControllerRunSummary(StrictBaseModel):
    """PDF入力からConsistency評価までの全体実行結果。"""

    input_pdf: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING
    completed: bool = False
    final_status: FinalizationStatus | None = None
    failed_stage: ControllerStage | None = None
    error_message: str | None = None
    fatal_error: bool = False
    human_review_required: bool = False
    human_review_input_received: bool = False
    human_review_approved: bool = False
    human_review_revision_requested: bool = False
    human_review_approved_after_revision: bool = False
    human_review_rounds: int = Field(default=0, ge=0)
    cross_consistency_finding_count: int = Field(default=0, ge=0)
    individual_rdf_issues: dict[str, IndividualRdfIssueSummary] = Field(
        default_factory=dict
    )
    stages: list[ControllerStageResult] = Field(default_factory=list)
    output_files: dict[str, str] = Field(default_factory=dict)


class FinalArtifactSummary(StrictBaseModel):
    """確定したRDFと最終検証状態の一覧。"""

    final_status: FinalizationStatus
    e2e_status: RunStatus
    final_rdf_files: dict[str, str]
    shacl_conforms: dict[str, bool]
    self_review_status: dict[str, str]
    cross_consistency_status: str
    human_review_performed: bool
    human_review_input_received: bool
    human_review_approved: bool
    human_review_revision_requested: bool
    human_review_approved_after_revision: bool
    human_review_rounds: int = Field(ge=1)
    human_review_revision_performed: bool
    revision_count: int = Field(ge=0)
    revision_counts: dict[str, int] = Field(default_factory=dict)
    final_rdf_hashes: dict[str, str]
    individual_rdf_issues: dict[str, IndividualRdfIssueSummary] = Field(
        default_factory=dict
    )
    unresolved_reason: str | None = None


class WorkflowAgentMode(str, Enum):
    """Workflow Agent internal processing mode."""

    WORKFLOW_GENERATION = "workflow_generation"
    WORKFLOW_SHACL_GENERATION = "workflow_shacl_generation"
    WORKFLOW_REVISION = "workflow_revision"
    WORKFLOW_SELF_REVIEW = "workflow_self_review"


class DataRuleAgentMode(str, Enum):
    """Related data and rule extraction agent internal processing mode."""

    DATA_GENERATION = "data_generation"
    DATA_SHACL_GENERATION = "data_shacl_generation"
    DATA_REVISION = "data_revision"
    DATA_SELF_REVIEW = "data_self_review"
    RULE_GENERATION = "rule_generation"
    RULE_SHACL_GENERATION = "rule_shacl_generation"
    RULE_REVISION = "rule_revision"
    RULE_SELF_REVIEW = "rule_self_review"


class ConsistencyAgentMode(str, Enum):
    """Consistency Agent internal processing mode."""

    CROSS_SHACL_GENERATION = "cross_shacl_generation"
    VIOLATION_ANALYSIS = "violation_analysis"


class SelfReviewCategory(str, Enum):
    """RDFと根拠文書の意味的不整合カテゴリ。"""

    MISSING_INFORMATION = "missing_information"
    UNSUPPORTED_INFORMATION = "unsupported_information"
    MISINTERPRETATION = "misinterpretation"
    INCORRECT_RELATION = "incorrect_relation"
    DUPLICATE_EXTRACTION = "duplicate_extraction"
    CONTRADICTION = "contradiction"


class SelfReviewRunStatus(str, Enum):
    """Self-Reviewループ全体の終了状態。"""

    PASSED = "passed"
    MAX_ITERATIONS = "max_iterations"
    SHACL_FAILED = "shacl_failed"


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
    """Scenario RDF生成エージェントに渡す入力。"""

    document: SourceDocument
    ontology_turtle: str = Field(min_length=1)


class ScenarioAgentOutput(StrictBaseModel):
    """Scenario Agentが直接生成した完全なTurtle文字列。"""

    scenario_rdf_turtle: str = Field(min_length=1)


class SelfReviewEvidence(StrictBaseModel):
    """Self-Review findingの根拠箇所。"""

    source: str = Field(min_length=1)
    locator: str | None = None
    excerpt: str = Field(min_length=1)


class SelfReviewFinding(StrictBaseModel):
    """Self-Reviewで検出した意味的な問題。"""

    category: SelfReviewCategory
    target: str | None = None
    description: str = Field(min_length=1)
    evidence: list[SelfReviewEvidence] = Field(min_length=1)
    revision_instruction: str = Field(min_length=1)
    severity: Severity = Severity.ERROR


class SelfReviewResult(StrictBaseModel):
    """抽出Agent自身によるRDFの意味的評価結果。"""

    reviewer_agent: AgentName
    rdf_kind: RdfKind
    passed: bool
    findings: list[SelfReviewFinding] = Field(default_factory=list)
    summary: str = Field(min_length=1)


class SelfReviewIteration(StrictBaseModel):
    """1回のSelf-Reviewと後続revisionの記録。"""

    iteration: int = Field(ge=0)
    rdf_hash: str = Field(min_length=1)
    result: SelfReviewResult
    revision_performed: bool = False
    post_revision_validation_conforms: bool | None = None


class SelfReviewHistory(StrictBaseModel):
    """対象RDFのSelf-Reviewループ全体の記録。"""

    rdf_kind: RdfKind
    status: SelfReviewRunStatus
    max_revision_iterations: int = Field(ge=0)
    iterations: list[SelfReviewIteration] = Field(default_factory=list)
    final_result: SelfReviewResult | None = None


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
    """Structured output for Workflow RDF and SHACL processing.

    The ontology is fixed. Generated SHACL is fixed by the pipeline before RDF
    validation and revision.
    """

    mode: WorkflowAgentMode
    workflow_rdf_turtle: str | None = None
    workflow_shacl_turtle: str | None = None
    used_vocabulary_terms: Any = Field(default_factory=list)
    unresolved_items: Any = Field(default_factory=list)
    generation_notes: Any = Field(default_factory=list)
    evidence_summary: Any = Field(default_factory=list)
    revision_summary: str | None = None
    addressed_violations: Any = Field(default_factory=list)
    remaining_violations: Any = Field(default_factory=list)
    self_review_result: SelfReviewResult | None = None


class DataRuleAgentOutput(StrictBaseModel):
    """Structured output for Data/Rule RDF and SHACL processing.

    Ontologies are fixed. Generated SHACL is fixed by the pipeline before RDF
    validation and revision.
    """

    mode: DataRuleAgentMode
    data_rdf_turtle: str | None = None
    rule_rdf_turtle: str | None = None
    data_shacl_turtle: str | None = None
    rule_shacl_turtle: str | None = None
    used_vocabulary_terms: Any = Field(default_factory=list)
    unresolved_items: Any = Field(default_factory=list)
    generation_notes: Any = Field(default_factory=list)
    evidence_summary: Any = Field(default_factory=list)
    revision_summary: str | None = None
    addressed_violations: Any = Field(default_factory=list)
    remaining_violations: Any = Field(default_factory=list)
    self_review_result: SelfReviewResult | None = None



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


class ShaclViolation(StrictBaseModel):
    """pySHACLの検証レポートから抽出したSHACL違反。"""

    focus_node: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: str | None = None
    source_shape: str | None = None
    constraint_component: str | None = None
    severity: Severity = Severity.ERROR
    rdf_kind: RdfKind | None = None


class ShaclValidationResult(StrictBaseModel):
    """pySHACLによる適合判定結果。"""

    conforms: bool
    violations: list[ShaclViolation] = Field(default_factory=list)
    report_text: str | None = None


class CrossRdfValidationResult(StrictBaseModel):
    """Syntax and Cross-SHACL result for the merged RDF graph."""

    structure_conforms: bool
    shacl_conforms: bool
    parse_errors: list[RdfParseError] = Field(default_factory=list)
    shacl_result: ShaclValidationResult | None = None

    @property
    def conforms(self) -> bool:
        """Return True only when parsing and Cross-SHACL validation pass."""

        return self.structure_conforms and self.shacl_conforms


class ConsistencyEvaluationInput(StrictBaseModel):
    """整合性評価エージェントに渡す入力。"""

    workflow_rdf_turtle: str = Field(min_length=1)
    data_rdf_turtle: str = Field(min_length=1)
    rule_rdf_turtle: str | None = None
    workflow_ontology_turtle: str = Field(min_length=1)
    data_ontology_turtle: str = Field(min_length=1)
    rule_ontology_turtle: str | None = None
    cross_shacl_turtle: str | None = None
    validation_result: ShaclValidationResult | None = None


class ConsistencyViolationAnalysis(StrictBaseModel):
    """LLM analysis and repair guidance for one Cross-SHACL violation."""

    violation_index: int = Field(ge=0)
    target_resource: str = Field(min_length=1)
    cause: str = Field(min_length=1)
    target_agent: AgentName
    repair_instruction: str = Field(min_length=1)


class ConsistencyAgentOutput(StrictBaseModel):
    """Structured output for Cross-SHACL generation or violation analysis."""

    mode: ConsistencyAgentMode
    cross_shacl_turtle: str | None = None
    violation_analyses: list[ConsistencyViolationAnalysis] = Field(default_factory=list)
    summary: str | None = None


class ConsistencyEvaluationResult(StrictBaseModel):
    """RDF間の不整合に対する評価結果。"""

    status: ReviewStatus
    conforms: bool = False
    can_auto_repair: bool
    target_agent: AgentName | None = None
    reason: str = Field(min_length=1)
    related_violation_indices: list[int] = Field(default_factory=list)
    violations: list[ShaclViolation] = Field(default_factory=list)
    violation_analyses: list[ConsistencyViolationAnalysis] = Field(default_factory=list)
    cross_shapes_hash: str | None = None


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


class HumanReviewFindingReference(StrictBaseModel):
    """Human Reviewから元Consistency findingを参照する情報。"""

    finding_id: str = Field(min_length=1)
    consistency_violation_index: int = Field(ge=0)
    target_resource: str = Field(min_length=1)
    source_violation: ShaclViolation
    source_analysis: ConsistencyViolationAnalysis


class HumanReviewFindingResult(HumanReviewFindingReference):
    """1件のConsistency findingに対する人間のレビュー結果。"""

    decision: HumanReviewDecision
    supplemental_comment: str | None = None


class HumanReviewGroupResult(StrictBaseModel):
    """同じ構造的条件を持つConsistency findingsのレビュー結果。"""

    group_id: str = Field(min_length=1)
    group_key: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)
    violation_type: str = Field(min_length=1)
    target_agent: AgentName
    result_path: str | None = None
    constraint_component: str | None = None
    source_shape: str | None = None
    common_cause: str = Field(min_length=1)
    common_repair_policy: str = Field(min_length=1)
    target_resources: list[str] = Field(min_length=1)
    decision: HumanReviewGroupDecision
    supplemental_comment: str | None = None
    individually_reviewed: bool = False
    source_findings: list[HumanReviewFindingReference] = Field(min_length=1)
    individual_results: list[HumanReviewFindingResult] = Field(default_factory=list)


class HumanReviewRevisionRequest(StrictBaseModel):
    """人間が対象Agentへ戻すことを明示した修正要求。"""

    target_agent: AgentName
    revision_instruction: str = Field(min_length=1)
    source_finding_ids: list[str] = Field(default_factory=list)
    target_resources: list[str] = Field(default_factory=list)


class HumanReviewDecisionRecord(StrictBaseModel):
    """Human Review 1回分の明示入力履歴。"""

    review_round: int = Field(ge=1)
    decision: HumanReviewFinalDecision
    approved: bool
    revision_requested: bool
    revision_requests: list[HumanReviewRevisionRequest] = Field(default_factory=list)
    reviewed_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def validate_decision(self) -> HumanReviewDecisionRecord:
        expects_approval = self.decision is HumanReviewFinalDecision.APPROVE
        if self.approved is not expects_approval:
            raise ValueError("Human Review history approval does not match decision.")
        if self.revision_requested is expects_approval:
            raise ValueError("Human Review history revision state does not match decision.")
        if self.revision_requested and not self.revision_requests:
            raise ValueError("A revision decision requires at least one revision request.")
        if not self.revision_requested and self.revision_requests:
            raise ValueError("An approval must not contain revision requests.")
        return self


class HumanReviewReport(StrictBaseModel):
    """Consistency評価全体に対するHuman Review成果物。"""

    status: ReviewStatus
    input_received: bool
    final_decision: HumanReviewFinalDecision
    approved: bool
    revision_requested: bool
    review_round: int = Field(ge=1)
    reviewer: str | None = None
    reviewed_at: datetime = Field(default_factory=datetime.now)
    consistency_status: ReviewStatus
    consistency_conforms: bool
    workflow_rdf_file: str = Field(min_length=1)
    data_rdf_file: str = Field(min_length=1)
    rule_rdf_file: str | None = None
    consistency_evaluation_file: str = Field(min_length=1)
    groups: list[HumanReviewGroupResult] = Field(default_factory=list)
    findings: list[HumanReviewFindingResult] = Field(default_factory=list)
    revision_requests: list[HumanReviewRevisionRequest] = Field(default_factory=list)
    decision_history: list[HumanReviewDecisionRecord] = Field(min_length=1)
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_explicit_decision(self) -> HumanReviewReport:
        """Keep approval, revision, and explicit-input fields consistent."""

        expects_approval = self.final_decision is HumanReviewFinalDecision.APPROVE
        if not self.input_received:
            raise ValueError("Human Review requires an explicit human input.")
        if self.approved is not expects_approval:
            raise ValueError("Human Review approval does not match final_decision.")
        if self.revision_requested is expects_approval:
            raise ValueError("Human Review revision state does not match final_decision.")
        expected_status = (
            ReviewStatus.APPROVED if expects_approval else ReviewStatus.NEEDS_REVISION
        )
        if self.status is not expected_status:
            raise ValueError("Human Review status does not match final_decision.")
        if self.revision_requested and not self.revision_requests:
            raise ValueError("A revision decision requires at least one revision request.")
        if not self.revision_requested and self.revision_requests:
            raise ValueError("An approval must not contain revision requests.")
        latest = self.decision_history[-1]
        if (
            latest.review_round != self.review_round
            or latest.decision is not self.final_decision
        ):
            raise ValueError("Human Review history does not contain the current decision.")
        return self


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
