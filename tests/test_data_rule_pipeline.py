"""Tests for Data RDF and Rule RDF generation without real API calls."""

from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace

import pymupdf
import pytest
from agents.exceptions import ModelBehaviorError

from business_analysis_agents.agents.data_rule import (
    DATA_RULE_AGENT_NAME,
    build_data_rule_agent,
    run_data_rule_agent,
)
from business_analysis_agents.data_rule_pipeline import run_data_rule_pipeline
from business_analysis_agents.models import (
    AgentName,
    DataRuleAgentMode,
    DataRuleAgentOutput,
    RdfKind,
    SelfReviewCategory,
    SelfReviewEvidence,
    SelfReviewFinding,
    SelfReviewResult,
)
from business_analysis_agents.rdf_validation import validate_ontology_and_shapes, validate_rdf
from business_analysis_agents.progress import ProgressReporter


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


def _scenario_turtle() -> str:
    return """
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix inst: <http://example.org/scenario/instance/> .

inst:application-subject dcterms:title "Application handling" ;
    dcterms:hasPart inst:submit-application .
inst:applicant a prov:Agent ; dcterms:title "Applicant" .
inst:submit-application a prov:Activity ;
    dcterms:title "Submit application" ;
    prov:wasAssociatedWith inst:applicant .
""".strip()


def _semantic_review(
    agent: AgentName,
    rdf_kind: RdfKind,
    passed: bool = True,
    findings: list[SelfReviewFinding] | None = None,
) -> SelfReviewResult:
    return SelfReviewResult(
        reviewer_agent=agent,
        rdf_kind=rdf_kind,
        passed=passed,
        findings=findings or [],
        summary="No semantic issues." if passed else "Document detail is missing.",
    )


def test_data_rule_agent_is_single_agent_with_eight_modes(monkeypatch) -> None:
    """One related data/rule agent handles generation through Self-Review."""

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
        if prompt.startswith("Mode: data_shacl_generation"):
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SHACL_GENERATION,
                    data_shacl_turtle=DATA_SHAPES_TTL,
                )
            )
        if "data_self_review" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SELF_REVIEW,
                    self_review_result=_semantic_review(AgentName.DATA, RdfKind.DATA),
                )
            )
        if "rule_revision" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_REVISION,
                    rule_rdf_turtle=VALID_RULE_TTL,
                )
            )
        if prompt.startswith("Mode: rule_shacl_generation"):
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_SHACL_GENERATION,
                    rule_shacl_turtle=RULE_SHAPES_TTL,
                )
            )
        if "rule_self_review" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_SELF_REVIEW,
                    self_review_result=_semantic_review(AgentName.RULE, RdfKind.RULE),
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
            payload={"scenario_rdf_turtle": _scenario_turtle()},
            model="gpt-test",
            runner=fake_runner,
        )
        assert output.mode == mode

    assert build_data_rule_agent("gpt-test").name == DATA_RULE_AGENT_NAME
    assert len(calls) == 8


@pytest.mark.parametrize(
    ("mode", "expected_message"),
    [
        (DataRuleAgentMode.DATA_GENERATION, "data_rdf_turtle"),
        (DataRuleAgentMode.DATA_SHACL_GENERATION, "data_shacl_turtle"),
        (DataRuleAgentMode.DATA_REVISION, "data_rdf_turtle"),
        (DataRuleAgentMode.RULE_GENERATION, "rule_rdf_turtle"),
        (DataRuleAgentMode.RULE_SHACL_GENERATION, "rule_shacl_turtle"),
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
        payload={"scenario_rdf_turtle": _scenario_turtle()},
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


def _write_source_pdf(tmp_path):
    pdf_file = tmp_path / "source.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "PDF detail: amount must be at least 1000 JPY.")
    document.save(pdf_file)
    document.close()
    return pdf_file


def test_pipeline_generates_data_then_rule_and_saves_outputs(monkeypatch, tmp_path) -> None:
    """Pipeline generates separate SHACL graphs after Data and Rule raw RDF."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    fixed_files = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)
    call_order: list[str] = []
    progress_stream = StringIO()
    progress = ProgressReporter(stream=progress_stream)

    def fake_runner(_agent, prompt):
        if "data_shacl_generation" in prompt:
            call_order.append("data_shacl_generation")
            assert "data_rdf_raw" in prompt
            assert "inst:data-application" in prompt
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SHACL_GENERATION,
                    data_shacl_turtle=DATA_SHAPES_TTL,
                )
            )
        if prompt.startswith("Mode: data_self_review"):
            call_order.append("data_self_review")
            assert "current_data_rdf" in prompt
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SELF_REVIEW,
                    self_review_result=_semantic_review(AgentName.DATA, RdfKind.DATA),
                )
            )
        if "rule_shacl_generation" in prompt:
            call_order.append("rule_shacl_generation")
            assert "rule_rdf_raw" in prompt
            assert "inst:rule-eligibility" in prompt
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_SHACL_GENERATION,
                    rule_shacl_turtle=RULE_SHAPES_TTL,
                )
            )
        if prompt.startswith("Mode: rule_self_review"):
            call_order.append("rule_self_review")
            assert "current_rule_rdf" in prompt
            assert "validated_data_rdf" in prompt
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_SELF_REVIEW,
                    self_review_result=_semantic_review(AgentName.RULE, RdfKind.RULE),
                )
            )
        assert "source_document" in prompt
        assert "PDF detail: amount must be at least 1000 JPY." in prompt
        assert "scenario_rdf_turtle" in prompt
        assert "Application handling" in prompt
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
        pdf_file=pdf_file,
        output_dir=tmp_path / "data_rule",
        runner=fake_runner,
        data_ontology_file=fixed_files[0],
        rule_ontology_file=fixed_files[1],
        progress=progress,
    )

    assert call_order == [
        "data_generation",
        "data_shacl_generation",
        "data_self_review",
        "rule_generation",
        "rule_shacl_generation",
        "rule_self_review",
    ]
    assert result["final_status"] == "completed"
    progress_output = progress_stream.getvalue()
    assert "[Phase 3/6] Data RDF" in progress_output
    assert "[RUN] Data RDF generation" in progress_output
    assert "[Phase 4/6] Rule RDF" in progress_output
    assert "[RUN] Rule RDF generation" in progress_output
    assert {path.name for path in (tmp_path / "data_rule").iterdir()} == {
        "data_final.ttl",
        "data_shapes_generated.ttl",
        "data_validation.json",
        "data_revision_history.json",
        "data_self_review.json",
        "rule_final.ttl",
        "rule_shapes_generated.ttl",
        "rule_validation.json",
        "rule_revision_history.json",
        "rule_self_review.json",
    }


def test_pipeline_saves_data_outputs_before_rule_processing(monkeypatch, tmp_path) -> None:
    """Completed Data artifacts remain available when Rule processing fails."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    fixed_files = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)

    def fake_runner(_agent, prompt):
        if "data_shacl_generation" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SHACL_GENERATION,
                    data_shacl_turtle=DATA_SHAPES_TTL,
                )
            )
        if "data_self_review" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SELF_REVIEW,
                    self_review_result=_semantic_review(AgentName.DATA, RdfKind.DATA),
                )
            )
        assert "source_document" in prompt
        assert "PDF detail: amount must be at least 1000 JPY." in prompt
        if "data_generation" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_GENERATION,
                    data_rdf_turtle=VALID_DATA_TTL,
                )
            )
        raise RuntimeError("rule processing failed")

    output_dir = tmp_path / "data_rule"
    with pytest.raises(RuntimeError, match="rule processing failed"):
        run_data_rule_pipeline(
            scenario_file,
            model="gpt-test",
            pdf_file=pdf_file,
            output_dir=output_dir,
            runner=fake_runner,
            data_ontology_file=fixed_files[0],
            rule_ontology_file=fixed_files[1],
        )

    assert {path.name for path in output_dir.iterdir()} == {
        "data_final.ttl",
        "data_shapes_generated.ttl",
        "data_validation.json",
        "data_revision_history.json",
        "data_self_review.json",
    }


def test_pipeline_runs_data_and_rule_revision_until_valid(monkeypatch, tmp_path) -> None:
    """Pipeline revises only RDF outputs when Data RDF or Rule RDF is invalid."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    fixed_files = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)
    data_revision_calls = 0
    rule_revision_calls = 0
    data_shacl_calls = 0
    rule_shacl_calls = 0

    def fake_runner(_agent, prompt):
        nonlocal data_revision_calls, rule_revision_calls
        nonlocal data_shacl_calls, rule_shacl_calls
        if "data_shacl_generation" in prompt:
            data_shacl_calls += 1
            assert "inst:data-application" in prompt
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SHACL_GENERATION,
                    data_shacl_turtle=DATA_SHAPES_TTL,
                )
            )
        if "data_self_review" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SELF_REVIEW,
                    self_review_result=_semantic_review(AgentName.DATA, RdfKind.DATA),
                )
            )
        if "rule_shacl_generation" in prompt:
            rule_shacl_calls += 1
            assert "inst:rule-eligibility" in prompt
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_SHACL_GENERATION,
                    rule_shacl_turtle=RULE_SHAPES_TTL,
                )
            )
        if "rule_self_review" in prompt:
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_SELF_REVIEW,
                    self_review_result=_semantic_review(AgentName.RULE, RdfKind.RULE),
                )
            )
        assert "source_document" in prompt
        assert "PDF detail: amount must be at least 1000 JPY." in prompt
        assert "scenario_rdf_turtle" in prompt
        assert "Application handling" in prompt
        if prompt.startswith("Mode: data_revision"):
            data_revision_calls += 1
            assert "dr:DataEntityShape" in prompt
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
        if prompt.startswith("Mode: rule_revision"):
            rule_revision_calls += 1
            assert "dr:BusinessRuleShape" in prompt
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
        pdf_file=pdf_file,
        output_dir=tmp_path / "data_rule",
        runner=fake_runner,
        data_ontology_file=fixed_files[0],
        rule_ontology_file=fixed_files[1],
    )

    assert result["final_status"] == "completed"
    assert data_revision_calls == 1
    assert rule_revision_calls == 1
    assert data_shacl_calls == 1
    assert rule_shacl_calls == 1
    assert "inst:data-application a dr:DataEntity" in (
        tmp_path / "data_rule" / "data_final.ttl"
    ).read_text(encoding="utf-8")


def test_data_and_rule_self_reviews_drive_separate_revisions(
    monkeypatch,
    tmp_path,
) -> None:
    """Data and Rule findings revise each RDF and keep each generated SHACL fixed."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    fixed_files = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)
    revised_data = VALID_DATA_TTL.replace("Application form", "Application amount form")
    revised_rule = VALID_RULE_TTL.replace(
        "Application form is submitted.",
        "Application amount is at least 1000 JPY.",
    )
    calls = {
        "data_shapes": 0,
        "rule_shapes": 0,
        "data_review": 0,
        "rule_review": 0,
        "data_revision": 0,
        "rule_revision": 0,
    }
    evidence = [
        SelfReviewEvidence(
            source="pdf",
            locator="page 1",
            excerpt="amount must be at least 1000 JPY",
        )
    ]
    data_finding = SelfReviewFinding(
        category=SelfReviewCategory.MISSING_INFORMATION,
        target="inst:data-application",
        description="The amount field is missing.",
        evidence=evidence,
        revision_instruction="Add the documented amount detail.",
    )
    rule_finding = SelfReviewFinding(
        category=SelfReviewCategory.MISINTERPRETATION,
        target="inst:rule-eligibility",
        description="The numeric eligibility condition is missing.",
        evidence=evidence,
        revision_instruction="Represent the documented 1000 JPY condition.",
    )

    def fake_runner(_agent, prompt):
        if prompt.startswith("Mode: data_shacl_generation"):
            calls["data_shapes"] += 1
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SHACL_GENERATION,
                    data_shacl_turtle=DATA_SHAPES_TTL,
                )
            )
        if prompt.startswith("Mode: rule_shacl_generation"):
            calls["rule_shapes"] += 1
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_SHACL_GENERATION,
                    rule_shacl_turtle=RULE_SHAPES_TTL,
                )
            )
        if prompt.startswith("Mode: data_self_review"):
            calls["data_review"] += 1
            assert "PDF detail: amount must be at least 1000 JPY." in prompt
            assert "current_data_rdf" in prompt
            result = (
                _semantic_review(AgentName.DATA, RdfKind.DATA, False, [data_finding])
                if calls["data_review"] == 1
                else _semantic_review(AgentName.DATA, RdfKind.DATA)
            )
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_SELF_REVIEW,
                    self_review_result=result,
                )
            )
        if prompt.startswith("Mode: rule_self_review"):
            calls["rule_review"] += 1
            assert "validated_data_rdf" in prompt
            assert "Application amount form" in prompt
            result = (
                _semantic_review(AgentName.RULE, RdfKind.RULE, False, [rule_finding])
                if calls["rule_review"] == 1
                else _semantic_review(AgentName.RULE, RdfKind.RULE)
            )
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_SELF_REVIEW,
                    self_review_result=result,
                )
            )
        if prompt.startswith("Mode: data_revision"):
            calls["data_revision"] += 1
            assert "Add the documented amount detail." in prompt
            assert "dr:DataEntityShape" in prompt
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_REVISION,
                    data_rdf_turtle=revised_data,
                )
            )
        if prompt.startswith("Mode: rule_revision"):
            calls["rule_revision"] += 1
            assert "Represent the documented 1000 JPY condition." in prompt
            assert "dr:BusinessRuleShape" in prompt
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.RULE_REVISION,
                    rule_rdf_turtle=revised_rule,
                )
            )
        if prompt.startswith("Mode: data_generation"):
            return SimpleNamespace(
                final_output=DataRuleAgentOutput(
                    mode=DataRuleAgentMode.DATA_GENERATION,
                    data_rdf_turtle=VALID_DATA_TTL,
                )
            )
        return SimpleNamespace(
            final_output=DataRuleAgentOutput(
                mode=DataRuleAgentMode.RULE_GENERATION,
                rule_rdf_turtle=VALID_RULE_TTL,
            )
        )

    output_dir = tmp_path / "data_rule"
    result = run_data_rule_pipeline(
        scenario_file,
        model="gpt-test",
        pdf_file=pdf_file,
        output_dir=output_dir,
        runner=fake_runner,
        data_ontology_file=fixed_files[0],
        rule_ontology_file=fixed_files[1],
    )

    assert result["final_status"] == "completed"
    assert calls == {
        "data_shapes": 1,
        "rule_shapes": 1,
        "data_review": 2,
        "rule_review": 2,
        "data_revision": 1,
        "rule_revision": 1,
    }
    assert (output_dir / "data_final.ttl").read_text(encoding="utf-8") == revised_data
    assert (output_dir / "rule_final.ttl").read_text(encoding="utf-8") == revised_rule
    for rdf_kind in ("data", "rule"):
        review = json.loads(
            (output_dir / f"{rdf_kind}_self_review.json").read_text(
                encoding="utf-8"
            )
        )
        history = json.loads(
            (output_dir / f"{rdf_kind}_revision_history.json").read_text(
                encoding="utf-8"
            )
        )
        assert review["status"] == "passed"
        assert len(review["iterations"]) == 2
        assert history[0]["phase"] == "self_review_revision"
