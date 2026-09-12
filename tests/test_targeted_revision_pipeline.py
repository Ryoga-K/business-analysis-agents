"""Tests for RDF-targeted revision after Cross Consistency findings."""

from __future__ import annotations

import json

import pymupdf
import pytest

from business_analysis_agents import targeted_revision_pipeline as pipeline
from business_analysis_agents.models import (
    AgentName,
    DataRuleAgentMode,
    DataRuleAgentOutput,
    RdfKind,
    SelfReviewResult,
    WorkflowAgentMode,
    WorkflowAgentOutput,
)
from business_analysis_agents.rdf_validation import content_hash


WORKFLOW_ONTOLOGY = """
@prefix wf: <http://example.org/workflow#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
wf:Activity a rdfs:Class .
wf:name a rdf:Property ; rdfs:domain wf:Activity ; rdfs:range xsd:string .
""".strip()

DATA_ONTOLOGY = """
@prefix data: <http://example.org/data#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
data:DataEntity a rdfs:Class .
data:name a rdf:Property ; rdfs:domain data:DataEntity ; rdfs:range xsd:string .
""".strip()

RULE_ONTOLOGY = """
@prefix rule: <http://example.org/rule#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
rule:Rule a rdfs:Class .
rule:text a rdf:Property ; rdfs:domain rule:Rule ; rdfs:range xsd:string .
""".strip()

WORKFLOW_SHAPES = """
@prefix wf: <http://example.org/workflow#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
wf:ActivityShape a sh:NodeShape ;
    sh:targetClass wf:Activity ;
    sh:property [ sh:path wf:name ; sh:minCount 1 ; sh:datatype xsd:string ] .
""".strip()

DATA_SHAPES = """
@prefix data: <http://example.org/data#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
data:EntityShape a sh:NodeShape ;
    sh:targetClass data:DataEntity ;
    sh:property [ sh:path data:name ; sh:minCount 1 ; sh:datatype xsd:string ] .
""".strip()

RULE_SHAPES = """
@prefix rule: <http://example.org/rule#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
rule:RuleShape a sh:NodeShape ;
    sh:targetClass rule:Rule ;
    sh:property [ sh:path rule:text ; sh:minCount 1 ; sh:datatype xsd:string ] .
""".strip()

WORKFLOW_RDF = """
@prefix wf: <http://example.org/workflow#> .
@prefix inst: <http://example.org/instance/> .
inst:activity a wf:Activity ; wf:name "Check application" .
""".strip()

DATA_RDF = """
@prefix data: <http://example.org/data#> .
@prefix inst: <http://example.org/instance/> .
inst:application a data:DataEntity ; data:name "Application" .
""".strip()

RULE_RDF = """
@prefix rule: <http://example.org/rule#> .
@prefix inst: <http://example.org/instance/> .
inst:rule a rule:Rule ; rule:text "Application is required" .
""".strip()

SCENARIO_RDF = """
@prefix dcterms: <http://purl.org/dc/terms/> .
<http://example.org/scenario> dcterms:title "Application handling" .
""".strip()


def _write_inputs(tmp_path):
    values = {
        "scenario.ttl": SCENARIO_RDF,
        "workflow.ttl": WORKFLOW_RDF,
        "data.ttl": DATA_RDF,
        "rule.ttl": RULE_RDF,
        "workflow_shapes.ttl": WORKFLOW_SHAPES,
        "data_shapes.ttl": DATA_SHAPES,
        "rule_shapes.ttl": RULE_SHAPES,
        "workflow_ontology.ttl": WORKFLOW_ONTOLOGY,
        "data_ontology.ttl": DATA_ONTOLOGY,
        "rule_ontology.ttl": RULE_ONTOLOGY,
    }
    paths = {}
    for name, value in values.items():
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        paths[name] = path
    for kind in ("workflow", "data", "rule"):
        paths[f"{kind}_validation.json"] = tmp_path / f"{kind}_validation.json"
        paths[f"{kind}_history.json"] = tmp_path / f"{kind}_history.json"
        paths[f"{kind}_review.json"] = tmp_path / f"{kind}_review.json"
        paths[f"{kind}_validation.json"].write_text(
            '{"conforms": true}',
            encoding="utf-8",
        )
        paths[f"{kind}_history.json"].write_text("[]", encoding="utf-8")
    pdf_path = tmp_path / "manual.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "An application is checked.")
    document.save(pdf_path)
    document.close()
    paths["pdf"] = pdf_path
    return paths


@pytest.mark.parametrize(
    ("target", "revision_mode", "review_mode", "rdf_field", "expected_rdf"),
    [
        (
            AgentName.WORKFLOW,
            WorkflowAgentMode.WORKFLOW_REVISION,
            WorkflowAgentMode.WORKFLOW_SELF_REVIEW,
            "workflow_rdf_turtle",
            WORKFLOW_RDF,
        ),
        (
            AgentName.DATA,
            DataRuleAgentMode.DATA_REVISION,
            DataRuleAgentMode.DATA_SELF_REVIEW,
            "data_rdf_turtle",
            DATA_RDF,
        ),
        (
            AgentName.RULE,
            DataRuleAgentMode.RULE_REVISION,
            DataRuleAgentMode.RULE_SELF_REVIEW,
            "rule_rdf_turtle",
            RULE_RDF,
        ),
    ],
)
def test_targeted_revision_validates_and_self_reviews_each_rdf(
    monkeypatch,
    tmp_path,
    target,
    revision_mode,
    review_mode,
    rdf_field,
    expected_rdf,
) -> None:
    """Every target uses one grouped revision, fixed SHACL, and Self-Review."""

    paths = _write_inputs(tmp_path)
    calls = []

    def make_review() -> SelfReviewResult:
        return SelfReviewResult(
            reviewer_agent=target,
            rdf_kind=RdfKind(target.value),
            passed=True,
            findings=[],
            summary="No semantic issue remains.",
        )

    def fake_workflow(mode, payload, model, runner=None):
        calls.append(mode)
        assert payload["scenario_rdf_turtle"] == SCENARIO_RDF
        assert "An application is checked." in str(payload["source_document"])
        if mode is WorkflowAgentMode.WORKFLOW_REVISION:
            assert payload["cross_consistency_revision"]["violation_indices"] == [0, 1]
            return WorkflowAgentOutput(
                mode=mode,
                workflow_rdf_turtle=WORKFLOW_RDF,
            )
        return WorkflowAgentOutput(
            mode=mode,
            self_review_result=make_review(),
        )

    def fake_data_rule(mode, payload, model, runner=None):
        calls.append(mode)
        assert payload["scenario_rdf_turtle"] == SCENARIO_RDF
        assert "An application is checked." in str(payload["source_document"])
        if mode in {
            DataRuleAgentMode.DATA_REVISION,
            DataRuleAgentMode.RULE_REVISION,
        }:
            assert payload["cross_consistency_revision"]["violation_indices"] == [0, 1]
            values = {rdf_field: expected_rdf}
            return DataRuleAgentOutput(mode=mode, **values)
        return DataRuleAgentOutput(
            mode=mode,
            self_review_result=make_review(),
        )

    monkeypatch.setattr(pipeline, "run_workflow_agent", fake_workflow)
    monkeypatch.setattr(pipeline, "run_data_rule_agent", fake_data_rule)
    bundle = {
        "target_agent": target.value,
        "violation_indices": [0, 1],
        "repair_instructions": ["Fix both references."],
        "items": [{"violation": {}, "analysis": {}}],
    }

    result = pipeline.run_targeted_rdf_revision(
        target_agent=target,
        repair_bundle=bundle,
        consistency_iteration=0,
        scenario_file=paths["scenario.ttl"],
        pdf_file=paths["pdf"],
        model="test-model",
        workflow_file=paths["workflow.ttl"],
        data_file=paths["data.ttl"],
        rule_file=paths["rule.ttl"],
        workflow_shapes_file=paths["workflow_shapes.ttl"],
        data_shapes_file=paths["data_shapes.ttl"],
        rule_shapes_file=paths["rule_shapes.ttl"],
        workflow_ontology_file=paths["workflow_ontology.ttl"],
        data_ontology_file=paths["data_ontology.ttl"],
        rule_ontology_file=paths["rule_ontology.ttl"],
        workflow_validation_file=paths["workflow_validation.json"],
        data_validation_file=paths["data_validation.json"],
        rule_validation_file=paths["rule_validation.json"],
        workflow_revision_history_file=paths["workflow_history.json"],
        data_revision_history_file=paths["data_history.json"],
        rule_revision_history_file=paths["rule_history.json"],
        workflow_self_review_file=paths["workflow_review.json"],
        data_self_review_file=paths["data_review.json"],
        rule_self_review_file=paths["rule_review.json"],
    )

    assert result["final_status"] == "completed"
    assert result["individual_shapes_hash"] == content_hash(
        {
            AgentName.WORKFLOW: WORKFLOW_SHAPES,
            AgentName.DATA: DATA_SHAPES,
            AgentName.RULE: RULE_SHAPES,
        }[target]
    )
    assert calls == [revision_mode, review_mode]
    history = json.loads(
        paths[f"{target.value}_history.json"].read_text(encoding="utf-8")
    )
    self_review = json.loads(
        paths[f"{target.value}_review.json"].read_text(encoding="utf-8")
    )
    assert history[0]["phase"] == "cross_consistency_revision"
    assert history[0]["cross_consistency_revision"]["violation_indices"] == [0, 1]
    assert history[0]["validation"]["conforms"]
    assert self_review["status"] == "passed"
