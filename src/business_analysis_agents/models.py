"""Pydantic models shared across agents, RDF conversion, and validation."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RdfKind(str, Enum):
    """Supported RDF graph categories."""

    WORKFLOW = "workflow"
    DATA = "data"
    RULE = "rule"


class SourceDocument(BaseModel):
    """Business document content supplied to extraction agents."""

    document_id: str
    title: str
    text: str


class ScenarioAgentInput(BaseModel):
    """Input for the scenario creation agent."""

    document: SourceDocument


class BusinessScenario(BaseModel):
    """Structured scenario extracted before RDF generation."""

    scenario_id: str
    name: str
    summary: str
    stakeholders: list[str] = Field(default_factory=list)


class ScenarioAgentOutput(BaseModel):
    """Output from the scenario creation agent."""

    scenarios: list[BusinessScenario] = Field(default_factory=list)


class WorkflowExtractionInput(BaseModel):
    """Input for the business workflow extraction agent."""

    document: SourceDocument
    scenarios: list[BusinessScenario] = Field(default_factory=list)


class WorkflowStep(BaseModel):
    """A structured business workflow step."""

    step_id: str
    name: str
    actor: str | None = None
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)


class WorkflowExtractionOutput(BaseModel):
    """Output from the workflow extraction agent."""

    steps: list[WorkflowStep] = Field(default_factory=list)


class DataRuleExtractionInput(BaseModel):
    """Input for data and rule extraction."""

    document: SourceDocument
    workflow_steps: list[WorkflowStep] = Field(default_factory=list)


class DataEntity(BaseModel):
    """A structured data entity used by the business process."""

    entity_id: str
    name: str
    attributes: list[str] = Field(default_factory=list)


class BusinessRule(BaseModel):
    """A structured business rule before RDF conversion."""

    rule_id: str
    description: str
    related_step_ids: list[str] = Field(default_factory=list)
    related_entity_ids: list[str] = Field(default_factory=list)


class DataRuleExtractionOutput(BaseModel):
    """Output from the data and rule extraction agent."""

    data_entities: list[DataEntity] = Field(default_factory=list)
    rules: list[BusinessRule] = Field(default_factory=list)


class ShaclViolation(BaseModel):
    """A single SHACL validation result."""

    focus_node: str
    message: str
    path: str | None = None
    rdf_kind: RdfKind | None = None


class ShaclValidationResult(BaseModel):
    """Result of pySHACL validation."""

    conforms: bool
    violations: list[ShaclViolation] = Field(default_factory=list)


class ConsistencyEvaluationInput(BaseModel):
    """Input for the consistency evaluation agent."""

    validation_result: ShaclValidationResult
    workflow: WorkflowExtractionOutput
    data_rules: DataRuleExtractionOutput


class ConsistencyEvaluationOutput(BaseModel):
    """Recommendation for automatic repair or human review."""

    can_auto_repair: bool
    target_agent: str | None = None
    reason: str
