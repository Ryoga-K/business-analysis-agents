"""Tests for Data RDF and Rule RDF generation without real API calls."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from agents.exceptions import ModelBehaviorError

from business_analysis_agents.agents.data_rule import (
    DATA_RULE_AGENT_NAME,
    build_data_rule_agent,
    run_data_rule_agent,
)
from business_analysis_agents.data_rule_pipeline import run_data_rule_pipeline
from business_analysis_agents.models import (
    BusinessProcedureStep,
    BusinessScenario,
    DataRuleAgentMode,
    DataRuleAgentOutput,
    EvidenceSpan,
    ScenarioAgentOutput,
)
from business_analysis_agents.rdf_validation import validate_ontology_and_shapes, validate_rdf
from business_analysis_agents.models import RdfKind


ONTOLOGY_TTL = """
@prefix dr: <http://example.org/data-rule#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

dr:DataEntity a rdfs:Class ; rdfs:label "DataEntity" .
dr:DataAttribute a rdfs:Class ; rdfs:label "DataAttribute" .
dr:Evidence a rdfs:Class ; rdfs:label "Evidence" .
dr:BusinessRule a rdfs:Class ; rdfs:label "BusinessRule" .
dr:Condition a rdfs:Class ; rdfs:label "Condition" .
dr:RuleOutcome a rdfs:Class ; rdfs:label "RuleOutcome" .

dr:label a rdf:Property ; rdfs:range xsd:string .
dr:hasAttribute a rdf:Property ; rdfs:domain dr:DataEntity ; rdfs:range dr:DataAttribute .
dr:attributeName a rdf:Property ; rdfs:domain dr:DataAttribute ; rdfs:range xsd:string .
dr:hasEvidence a rdf:Property ; rdfs:range dr:Evidence .
dr:sourcePage a rdf:Property ; rdfs:domain dr:Evidence ; rdfs:range xsd:integer .
dr:evidenceText a rdf:Property ; rdfs:domain dr:Evidence ; rdfs:range xsd:string .
dr:hasCondition a rdf:Property ; rdfs:domain dr:BusinessRule ; rdfs:range dr:Condition .
dr:conditionText a rdf:Property ; rdfs:domain dr:Condition ; rdfs:range xsd:string .
dr:usesData a rdf:Property ; rdfs:domain dr:BusinessRule ; rdfs:range dr:DataEntity .
dr:producesResult a rdf:Property ; rdfs:domain dr:BusinessRule ; rdfs:range dr:RuleOutcome .
dr:outcomeText a rdf:Property ; rdfs:domain dr:RuleOutcome ; rdfs:range xsd:string .
""".strip()


DATA_SHAPES_TTL = """
@prefix dr: <http://example.org/data-rule#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

dr:DataEntityShape a sh:NodeShape ;
    sh:targetClass dr:DataEntity ;
    sh:property [ sh:path dr:label ; sh:minCount 1 ; sh:datatype xsd:string ] ;
    sh:property [ sh:path dr:hasEvidence ; sh:minCount 1 ; sh:class dr:Evidence ] .

dr:EvidenceShape a sh:NodeShape ;
    sh:targetClass dr:Evidence ;
    sh:property [ sh:path dr:sourcePage ; sh:minCount 1 ; sh:datatype xsd:integer ] ;
    sh:property [ sh:path dr:evidenceText ; sh:minCount 1 ; sh:datatype xsd:string ] .
""".strip()


RULE_SHAPES_TTL = """
@prefix dr: <http://example.org/data-rule#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

dr:BusinessRuleShape a sh:NodeShape ;
    sh:targetClass dr:BusinessRule ;
    sh:property [ sh:path dr:label ; sh:minCount 1 ; sh:datatype xsd:string ] ;
    sh:property [ sh:path dr:hasCondition ; sh:minCount 1 ; sh:class dr:Condition ] ;
    sh:property [ sh:path dr:usesData ; sh:class dr:DataEntity ] ;
    sh:property [ sh:path dr:hasEvidence ; sh:minCount 1 ; sh:class dr:Evidence ] .

dr:ConditionShape a sh:NodeShape ;
    sh:targetClass dr:Condition ;
    sh:property [ sh:path dr:conditionText ; sh:minCount 1 ; sh:datatype xsd:string ] .

dr:EvidenceShape a sh:NodeShape ;
    sh:targetClass dr:Evidence ;
    sh:property [ sh:path dr:sourcePage ; sh:minCount 1 ; sh:datatype xsd:integer ] ;
    sh:property [ sh:path dr:evidenceText ; sh:minCount 1 ; sh:datatype xsd:string ] .
""".strip()


VALID_DATA_TTL = """
@prefix dr: <http://example.org/data-rule#> .
@prefix inst: <http://example.org/data-rule#id/> .

inst:data-application a dr:DataEntity ;
    dr:label "Application form" ;
    dr:hasEvidence inst:evidence-data-1 .

inst:evidence-data-1 a dr:Evidence ;
    dr:sourcePage 1 ;
    dr:evidenceText "Applicant submits an application form." .
""".strip()


INVALID_DATA_TTL = """
@prefix dr: <http://example.org/data-rule#> .
@prefix inst: <http://example.org/data-rule#id/> .

inst:data-application a dr:DataEntity .
""".strip()


VALID_RULE_TTL = """
@prefix dr: <http://example.org/data-rule#> .
@prefix inst: <http://example.org/data-rule#id/> .

inst:rule-eligibility a dr:BusinessRule ;
    dr:label "Application eligibility" ;
    dr:hasCondition inst:condition-eligibility ;
    dr:usesData inst:data-application ;
    dr:hasEvidence inst:evidence-rule-1 .

inst:condition-eligibility a dr:Condition ;
    dr:conditionText "Application form is submitted." .

inst:evidence-rule-1 a dr:Evidence ;
    dr:sourcePage 1 ;
    dr:evidenceText "Applicant submits an application form." .
""".strip()


INVALID_RULE_TTL = """
@prefix dr: <http://example.org/data-rule#> .
@prefix inst: <http://example.org/data-rule#id/> .

inst:rule-eligibility a dr:BusinessRule ;
    dr:label "Application eligibility" .
""".strip()


def _scenario() -> ScenarioAgentOutput:
    return ScenarioAgentOutput(
        scenarios=[
            BusinessScenario(
                scenario_id="SCN1",
                business_name="Application handling",
                business_goal="Handle application.",
                business_overview="Applicant submits an application form.",
                procedure_steps=[
                    BusinessProcedureStep(
                        step_id="S1",
                        step_name="Submit application",
                        description="Applicant submits an application form.",
                        actor="applicant",
                        input_data=["application form"],
                        output_data=["submitted application"],
                        evidence=[
                            EvidenceSpan(
                                page_number=1,
                                text="Applicant submits an application form.",
                            )
                        ],
                    )
                ],
            )
        ]
    )


def test_data_rule_agent_is_single_agent_with_four_modes(monkeypatch) -> None:
    """Only one related data/rule agent is reused by RDF generation and revision modes."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls: list[str] = []

    def fake_runner(agent, prompt):
        calls.append(prompt)
        assert agent.name == DATA_RULE_AGENT_NAME
        assert agent.output_type.output_type is DataRuleAgentOutput
        if "data_revision" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_REVISION,
                    data_rdf_turtle=VALID_DATA_TTL,
                )
            )
        if "data_generation" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_GENERATION,
                    data_rdf_turtle=VALID_DATA_TTL,
                )
            )
        if "rule_revision" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_REVISION,
                    rule_rdf_turtle=VALID_RULE_TTL,
                )
            )
        return SimpleNamespace(
            final_output=DataRuleAgentOutput(
                mode=DataRuleAgentMode.RULE_GENERATION,
                rule_rdf_turtle=VALID_RULE_TTL,
            )
        )

    for mode in DataRuleAgentMode:
        output = run_data_rule_agent(
            mode,
            payload={"scenario_json": {}},
            model="gpt-test",
            runner=fake_runner,
        )
        assert output.mode == mode

    assert build_data_rule_agent("gpt-test").name == DATA_RULE_AGENT_NAME
    assert len(calls) == 4


@pytest.mark.parametrize(
    ("mode", "expected_message"),
    [
        (DataRuleAgentMode.DATA_GENERATION, "data_rdf_turtle"),
        (DataRuleAgentMode.DATA_REVISION, "data_rdf_turtle"),
        (DataRuleAgentMode.RULE_GENERATION, "rule_rdf_turtle"),
        (DataRuleAgentMode.RULE_REVISION, "rule_rdf_turtle"),
    ],
)
def test_data_rule_output_allows_missing_turtle_until_pipeline_check(
    mode: DataRuleAgentMode,
    expected_message: str,
) -> None:
    """SDK parsing should not fail before the pipeline can report missing Turtle."""

    output = DataRuleAgentOutput(mode=mode)

    assert output.mode == mode
    assert expected_message.endswith("_turtle")


def test_data_rule_output_keeps_explanatory_fields_flexible() -> None:
    """Design notes and mappings may use flexible JSON structures."""

    output = DataRuleAgentOutput(
        mode=DataRuleAgentMode.DATA_GENERATION,
        data_rdf_turtle=VALID_DATA_TTL,
        generation_notes={"warnings": [{"data": "application form"}]},
        unresolved_items={"by_data": {"application form": ["attributes unknown"]}},
    )

    dumped = output.model_dump(mode="json")

    assert dumped["generation_notes"]["warnings"][0]["data"] == "application form"


def test_data_rule_agent_recovers_duplicate_key_output(monkeypatch) -> None:
    """Recover first non-null Turtle when SDK validation error includes duplicate keys."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_runner(_agent, _prompt):
        raise ModelBehaviorError(
            'Invalid JSON when parsing {"mode":"rule_generation",'
            f'"rule_rdf_turtle":{json.dumps(VALID_RULE_TTL)},'
            '"rule_rdf_turtle":null} for TypeAdapter(DataRuleAgentOutput)'
        )

    output = run_data_rule_agent(
        DataRuleAgentMode.RULE_GENERATION,
        payload={"scenario_json": {}},
        model="gpt-test",
        runner=fake_runner,
    )

    assert output.rule_rdf_turtle == VALID_RULE_TTL


def test_data_and_rule_ontology_shapes_validate() -> None:
    """Data/Rule ontology and both SHACL shape graphs can be parsed."""

    data_validation = validate_ontology_and_shapes(ONTOLOGY_TTL, DATA_SHAPES_TTL)
    rule_validation = validate_ontology_and_shapes(ONTOLOGY_TTL, RULE_SHAPES_TTL)

    assert data_validation.conforms
    assert rule_validation.conforms


def test_data_rdf_and_rule_rdf_validate_with_pyshacl() -> None:
    """Data RDF and Rule RDF are validated by RDFLib and pySHACL."""

    data_validation = validate_rdf(
        VALID_DATA_TTL,
        ONTOLOGY_TTL,
        DATA_SHAPES_TTL,
        rdf_kind=RdfKind.DATA,
    )
    rule_validation = validate_rdf(
        VALID_RULE_TTL,
        ONTOLOGY_TTL,
        RULE_SHAPES_TTL,
        rdf_kind=RdfKind.RULE,
        additional_data_turtle=VALID_DATA_TTL,
    )

    assert data_validation.conforms
    assert rule_validation.conforms


def test_rule_rdf_validation_can_use_data_rdf_references() -> None:
    """Rule RDF can reference Data RDF nodes during SHACL validation."""

    validation = validate_rdf(
        VALID_RULE_TTL,
        ONTOLOGY_TTL,
        RULE_SHAPES_TTL,
        rdf_kind=RdfKind.RULE,
        additional_data_turtle=VALID_DATA_TTL,
    )

    assert validation.conforms


def _write_fixed_files(tmp_path):
    data_ontology_file = tmp_path / "data_ontology.ttl"
    rule_ontology_file = tmp_path / "rule_ontology.ttl"
    data_shapes_file = tmp_path / "data_shapes.ttl"
    rule_shapes_file = tmp_path / "rule_shapes.ttl"
    data_ontology_file.write_text(ONTOLOGY_TTL, encoding="utf-8")
    rule_ontology_file.write_text(ONTOLOGY_TTL, encoding="utf-8")
    data_shapes_file.write_text(DATA_SHAPES_TTL, encoding="utf-8")
    rule_shapes_file.write_text(RULE_SHAPES_TTL, encoding="utf-8")
    return data_ontology_file, rule_ontology_file, data_shapes_file, rule_shapes_file


def test_pipeline_generates_data_then_rule_and_saves_outputs(monkeypatch, tmp_path) -> None:
    """Pipeline saves Data RDF, Rule RDF, validation, history, and metadata."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(_scenario().model_dump_json(), encoding="utf-8")
    fixed_files = _write_fixed_files(tmp_path)
    call_order: list[str] = []

    def fake_runner(_agent, prompt):
        if "data_generation" in prompt:
            call_order.append("data_generation")
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_GENERATION,
                    data_rdf_turtle=VALID_DATA_TTL,
                )
            )
        call_order.append("rule_generation")
        assert "validated_data_rdf" in prompt
        return SimpleNamespace(
            final_output=DataRuleAgentOutput(
                mode=DataRuleAgentMode.RULE_GENERATION,
                rule_rdf_turtle=VALID_RULE_TTL,
            )
        )

    result = run_data_rule_pipeline(
        scenario_file,
        model="gpt-test",
        output_dir=tmp_path / "data_rule",
        runner=fake_runner,
        data_ontology_file=fixed_files[0],
        rule_ontology_file=fixed_files[1],
        data_shapes_file=fixed_files[2],
        rule_shapes_file=fixed_files[3],
    )

    assert call_order == ["data_generation", "rule_generation"]
    assert result["final_status"] == "completed"
    assert (tmp_path / "data_rule" / "data_ontology_v0_1.ttl").exists()
    assert (tmp_path / "data_rule" / "rule_ontology_v0_1.ttl").exists()
    assert (tmp_path / "data_rule" / "data_shapes_v0_1.ttl").exists()
    assert (tmp_path / "data_rule" / "rule_shapes_v0_1.ttl").exists()
    assert (tmp_path / "data_rule" / "data_initial.ttl").exists()
    assert (tmp_path / "data_rule" / "data_final.ttl").exists()
    assert (tmp_path / "data_rule" / "rule_initial.ttl").exists()
    assert (tmp_path / "data_rule" / "rule_final.ttl").exists()
    assert (tmp_path / "data_rule" / "data_validation.json").exists()
    assert (tmp_path / "data_rule" / "rule_validation.json").exists()
    assert (tmp_path / "data_rule" / "data_rule_run_metadata.json").exists()


def test_pipeline_runs_data_and_rule_revision_until_valid(monkeypatch, tmp_path) -> None:
    """Pipeline revises only RDF outputs when Data RDF or Rule RDF is invalid."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(_scenario().model_dump_json(), encoding="utf-8")
    fixed_files = _write_fixed_files(tmp_path)
    data_revision_calls = 0
    rule_revision_calls = 0

    def fake_runner(_agent, prompt):
        nonlocal data_revision_calls, rule_revision_calls
        if "data_revision" in prompt:
            data_revision_calls += 1
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_REVISION,
                    data_rdf_turtle=VALID_DATA_TTL,
                )
            )
        if "data_generation" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_GENERATION,
                    data_rdf_turtle=INVALID_DATA_TTL,
                )
            )
        if "rule_revision" in prompt:
            rule_revision_calls += 1
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_REVISION,
                    rule_rdf_turtle=VALID_RULE_TTL,
                )
            )
        return SimpleNamespace(
            final_output=DataRuleAgentOutput(
                mode=DataRuleAgentMode.RULE_GENERATION,
                rule_rdf_turtle=INVALID_RULE_TTL,
            )
        )

    result = run_data_rule_pipeline(
        scenario_file,
        model="gpt-test",
        output_dir=tmp_path / "data_rule",
        max_data_iterations=2,
        max_rule_iterations=2,
        runner=fake_runner,
        data_ontology_file=fixed_files[0],
        rule_ontology_file=fixed_files[1],
        data_shapes_file=fixed_files[2],
        rule_shapes_file=fixed_files[3],
    )

    assert result["final_status"] == "completed"
    assert data_revision_calls == 1
    assert rule_revision_calls == 1
    assert "inst:data-application a dr:DataEntity" in (
        tmp_path / "data_rule" / "data_final.ttl"
    ).read_text(encoding="utf-8")
