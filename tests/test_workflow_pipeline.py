"""Tests for Workflow RDF generation without real API calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from business_analysis_agents.agents.workflow import (
    WORKFLOW_AGENT_NAME,
    build_workflow_agent,
    run_workflow_agent,
)
from business_analysis_agents.models import (
    BusinessProcedureStep,
    BusinessScenario,
    EvidenceSpan,
    ScenarioAgentOutput,
    WorkflowAgentMode,
    WorkflowAgentOutput,
)
from business_analysis_agents.rdf_validation import (
    content_hash,
    validate_ontology_and_shapes,
    validate_workflow_rdf,
)
from business_analysis_agents.workflow_pipeline import run_workflow_pipeline


ONTOLOGY_TTL = """
@prefix wf: <http://example.org/workflow#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

wf:Workflow a rdfs:Class ; rdfs:label "Workflow" .
wf:Activity a rdfs:Class ; rdfs:label "Activity" .
wf:Actor a rdfs:Class ; rdfs:label "Actor" .
wf:DataObject a rdfs:Class ; rdfs:label "DataObject" .
wf:Evidence a rdfs:Class ; rdfs:label "Evidence" .

wf:hasActivity a rdf:Property ;
    rdfs:domain wf:Workflow ;
    rdfs:range wf:Activity .
wf:stepName a rdf:Property ;
    rdfs:domain wf:Activity ;
    rdfs:range xsd:string .
wf:performedBy a rdf:Property ;
    rdfs:domain wf:Activity ;
    rdfs:range wf:Actor .
wf:hasInput a rdf:Property ;
    rdfs:domain wf:Activity ;
    rdfs:range wf:DataObject .
wf:hasOutput a rdf:Property ;
    rdfs:domain wf:Activity ;
    rdfs:range wf:DataObject .
wf:hasEvidence a rdf:Property ;
    rdfs:domain wf:Activity ;
    rdfs:range wf:Evidence .
wf:sourcePage a rdf:Property ;
    rdfs:domain wf:Evidence ;
    rdfs:range xsd:integer .
wf:evidenceText a rdf:Property ;
    rdfs:domain wf:Evidence ;
    rdfs:range xsd:string .
""".strip()


SHAPES_TTL = """
@prefix wf: <http://example.org/workflow#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

wf:ActivityShape a sh:NodeShape ;
    sh:targetClass wf:Activity ;
    sh:property [
        sh:path wf:stepName ;
        sh:minCount 1 ;
        sh:datatype xsd:string ;
    ] ;
    sh:property [
        sh:path wf:hasEvidence ;
        sh:minCount 1 ;
        sh:class wf:Evidence ;
    ] .

wf:EvidenceShape a sh:NodeShape ;
    sh:targetClass wf:Evidence ;
    sh:property [
        sh:path wf:sourcePage ;
        sh:minCount 1 ;
        sh:datatype xsd:integer ;
    ] ;
    sh:property [
        sh:path wf:evidenceText ;
        sh:minCount 1 ;
        sh:datatype xsd:string ;
    ] .
""".strip()


VALID_WORKFLOW_TTL = """
@prefix wf: <http://example.org/workflow#> .
@prefix inst: <http://example.org/workflow#id/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

inst:workflow-1 a wf:Workflow ;
    wf:hasActivity inst:activity-S1 .

inst:activity-S1 a wf:Activity ;
    wf:stepName "Check order" ;
    wf:performedBy inst:actor-clerk ;
    wf:hasInput inst:data-order ;
    wf:hasOutput inst:data-checked-order ;
    wf:hasEvidence inst:evidence-S1 .

inst:actor-clerk a wf:Actor .
inst:data-order a wf:DataObject .
inst:data-checked-order a wf:DataObject .

inst:evidence-S1 a wf:Evidence ;
    wf:sourcePage 1 ;
    wf:evidenceText "A clerk checks an order." .
""".strip()


INVALID_WORKFLOW_TTL = """
@prefix wf: <http://example.org/workflow#> .
@prefix inst: <http://example.org/workflow#id/> .

inst:activity-S1 a wf:Activity ;
    wf:stepName "Check order" .
""".strip()


def _scenario() -> ScenarioAgentOutput:
    return ScenarioAgentOutput(
        scenarios=[
            BusinessScenario(
                scenario_id="SCN1",
                business_name="Order handling",
                business_goal="Process orders.",
                business_overview="A clerk checks an order.",
                procedure_steps=[
                    BusinessProcedureStep(
                        step_id="S1",
                        step_name="Check order",
                        description="A clerk checks an order.",
                        actor="clerk",
                        input_data=["order"],
                        output_data=["checked order"],
                        evidence=[
                            EvidenceSpan(
                                page_number=1,
                                text="A clerk checks an order.",
                            )
                        ],
                    )
                ],
            )
        ]
    )


def test_workflow_agent_is_single_agent_with_three_modes(monkeypatch) -> None:
    """Only one Workflow Agent is defined and it is reused by all modes."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls: list[str] = []

    def fake_runner(agent, prompt):
        calls.append(prompt)
        assert agent.name == WORKFLOW_AGENT_NAME
        assert agent.output_type.output_type is WorkflowAgentOutput
        if "ontology_setup" in prompt:
            mode = WorkflowAgentMode.ONTOLOGY_SETUP
        elif "workflow_generation" in prompt:
            mode = WorkflowAgentMode.WORKFLOW_GENERATION
        else:
            mode = WorkflowAgentMode.WORKFLOW_REVISION
        return SimpleNamespace(final_output=WorkflowAgentOutput(mode=mode))

    for mode in WorkflowAgentMode:
        output = run_workflow_agent(
            mode,
            payload={"scenario_json": {}},
            model="gpt-test",
            runner=fake_runner,
        )
        assert output.mode == mode

    assert build_workflow_agent("gpt-test").name == WORKFLOW_AGENT_NAME
    assert len(calls) == 3


def test_ontology_setup_output_can_be_validated() -> None:
    """Generated ontology_turtle and shacl_turtle can be parsed and fixed."""

    validation = validate_ontology_and_shapes(ONTOLOGY_TTL, SHAPES_TTL)

    assert validation.conforms
    assert validation.ontology_hash == content_hash(ONTOLOGY_TTL)
    assert validation.shapes_hash == content_hash(SHAPES_TTL)


def test_workflow_agent_output_accepts_nested_mapping() -> None:
    """Ontology setup notes may include nested JSON mapping objects."""

    output = WorkflowAgentOutput(
        mode=WorkflowAgentMode.ONTOLOGY_SETUP,
        class_property_mapping={
            "Workflow": ["wf:scenarioId", "wf:hasActivity"],
            "scenario_field_mapping": {
                "scenario_id": "wf:scenarioId",
                "procedure_steps": "wf:hasActivity",
            },
        },
    )

    dumped = output.model_dump(mode="json")

    assert dumped["class_property_mapping"]["scenario_field_mapping"]["scenario_id"] == (
        "wf:scenarioId"
    )


def test_invalid_ontology_turtle_is_detected() -> None:
    """Malformed ontology_turtle is reported as an RDFLib parse error."""

    validation = validate_ontology_and_shapes("not turtle", SHAPES_TTL)

    assert not validation.conforms
    assert validation.parse_errors


def test_shapes_with_undefined_terms_are_detected() -> None:
    """Shapes must not reference undefined ontology terms."""

    bad_shapes = SHAPES_TTL.replace("wf:stepName", "wf:missingProperty")
    validation = validate_ontology_and_shapes(ONTOLOGY_TTL, bad_shapes)

    assert not validation.conforms
    assert any("missingProperty" in term for term in validation.undefined_references)


def test_workflow_rdf_parse_error_is_detected() -> None:
    """Malformed Workflow RDF is detected before pySHACL revision feedback."""

    validation = validate_workflow_rdf("not turtle", ONTOLOGY_TTL, SHAPES_TTL)

    assert not validation.conforms
    assert validation.parse_errors


def test_unauthorized_vocabulary_terms_are_detected() -> None:
    """Workflow RDF must not introduce terms outside the fixed ontology."""

    workflow = VALID_WORKFLOW_TTL + "\ninst:activity-S1 wf:unknownTerm \"x\" ."
    validation = validate_workflow_rdf(workflow, ONTOLOGY_TTL, SHAPES_TTL)

    assert not validation.conforms
    assert validation.vocabulary.unauthorized_term_count == 1


def test_shacl_violations_are_detected() -> None:
    """pySHACL violations are exposed in Workflow RDF validation."""

    validation = validate_workflow_rdf(INVALID_WORKFLOW_TTL, ONTOLOGY_TTL, SHAPES_TTL)

    assert not validation.conforms
    assert validation.shacl_result is not None
    assert validation.shacl_result.violations


def test_pipeline_stops_when_conforms_true(monkeypatch, tmp_path) -> None:
    """Pipeline saves final artifacts and stops without revision when SHACL conforms."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(_scenario().model_dump_json(), encoding="utf-8")

    def fake_runner(_agent, prompt):
        if "ontology_setup" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.ONTOLOGY_SETUP,
                    ontology_turtle=ONTOLOGY_TTL,
                    shacl_turtle=SHAPES_TTL,
                    ontology_version="0.1",
                    namespace_uri="http://example.org/workflow#",
                )
            )
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                workflow_rdf_turtle=VALID_WORKFLOW_TTL,
            )
        )

    result = run_workflow_pipeline(
        scenario_file,
        model="gpt-test",
        output_dir=tmp_path / "workflow",
        runner=fake_runner,
    )

    assert result["final_status"] == "completed"
    assert (tmp_path / "workflow" / "workflow_final.ttl").exists()
    assert (tmp_path / "workflow" / "workflow_run_metadata.json").exists()


def test_pipeline_runs_revision_until_max_iterations(monkeypatch, tmp_path) -> None:
    """Pipeline stops at max workflow revisions when violations remain."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(_scenario().model_dump_json(), encoding="utf-8")
    revision_calls = 0

    def fake_runner(_agent, prompt):
        nonlocal revision_calls
        if "ontology_setup" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.ONTOLOGY_SETUP,
                    ontology_turtle=ONTOLOGY_TTL,
                    shacl_turtle=SHAPES_TTL,
                    ontology_version="0.1",
                    namespace_uri="http://example.org/workflow#",
                )
            )
        if "workflow_revision" in prompt:
            revision_calls += 1
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_REVISION,
                    workflow_rdf_turtle=INVALID_WORKFLOW_TTL,
                    remaining_violations=["missing evidence"],
                )
            )
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                workflow_rdf_turtle=INVALID_WORKFLOW_TTL,
            )
        )

    result = run_workflow_pipeline(
        scenario_file,
        model="gpt-test",
        output_dir=tmp_path / "workflow",
        max_workflow_iterations=2,
        runner=fake_runner,
    )

    assert result["final_status"] == "needs_review"
    assert revision_calls == 2


def test_pipeline_does_not_autofill_business_triples(monkeypatch, tmp_path) -> None:
    """Python pipeline does not create business RDF triples by itself."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(_scenario().model_dump_json(), encoding="utf-8")
    generated = "this is not turtle"

    def fake_runner(_agent, prompt):
        if "ontology_setup" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.ONTOLOGY_SETUP,
                    ontology_turtle=ONTOLOGY_TTL,
                    shacl_turtle=SHAPES_TTL,
                    ontology_version="0.1",
                    namespace_uri="http://example.org/workflow#",
                )
            )
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                workflow_rdf_turtle=generated,
            )
        )

    run_workflow_pipeline(
        scenario_file,
        model="gpt-test",
        output_dir=tmp_path / "workflow",
        max_workflow_iterations=0,
        runner=fake_runner,
    )

    assert (tmp_path / "workflow" / "workflow_final.ttl").read_text(
        encoding="utf-8"
    ) == generated
