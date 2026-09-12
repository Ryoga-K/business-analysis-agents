"""Tests for Workflow RDF generation without real API calls."""

from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace

import pymupdf
import pytest
from agents.exceptions import ModelBehaviorError

from business_analysis_agents.agents.workflow import (
    WORKFLOW_AGENT_NAME,
    build_workflow_agent,
    run_workflow_agent,
)
from business_analysis_agents.config import MAX_REVISION_ITERATIONS
from business_analysis_agents.models import (
    AgentName,
    RdfKind,
    SelfReviewCategory,
    SelfReviewEvidence,
    SelfReviewFinding,
    SelfReviewResult,
    WorkflowAgentMode,
    WorkflowAgentOutput,
)
from business_analysis_agents.rdf_validation import (
    content_hash,
    validate_ontology_and_shapes,
    validate_workflow_rdf,
)
from business_analysis_agents.progress import ProgressReporter
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


def _scenario_turtle() -> str:
    return """
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix inst: <http://example.org/scenario/instance/> .

inst:order-subject dcterms:title "Order handling" ;
    dcterms:hasPart inst:check-order .
inst:clerk a prov:Agent ; dcterms:title "Clerk" .
inst:check-order a prov:Activity ;
    dcterms:title "Check order" ;
    prov:wasAssociatedWith inst:clerk .
""".strip()


def _workflow_review(
    passed: bool = True,
    findings: list[SelfReviewFinding] | None = None,
) -> SelfReviewResult:
    return SelfReviewResult(
        reviewer_agent=AgentName.WORKFLOW,
        rdf_kind=RdfKind.WORKFLOW,
        passed=passed,
        findings=findings or [],
        summary="No semantic issues." if passed else "A workflow step is missing.",
    )


def test_workflow_agent_is_single_agent_with_four_modes(monkeypatch) -> None:
    """One Workflow Agent handles generation, SHACL, revision, and Self-Review."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls: list[str] = []

    def fake_runner(agent, prompt):
        calls.append(prompt)
        assert agent.name == WORKFLOW_AGENT_NAME
        assert agent.output_type.output_type is WorkflowAgentOutput
        if "workflow_generation" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                    workflow_rdf_turtle=VALID_WORKFLOW_TTL,
                )
            )
        if "workflow_shacl_generation" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SHACL_GENERATION,
                    workflow_shacl_turtle=SHAPES_TTL,
                )
            )
        if "workflow_self_review" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SELF_REVIEW,
                    self_review_result=_workflow_review(),
                )
            )
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_REVISION,
                workflow_rdf_turtle=VALID_WORKFLOW_TTL,
            )
        )

    for mode in WorkflowAgentMode:
        output = run_workflow_agent(
            mode,
            payload={"scenario_rdf_turtle": _scenario_turtle()},
            model="gpt-test",
            runner=fake_runner,
        )
        assert output.mode == mode

    assert build_workflow_agent("gpt-test").name == WORKFLOW_AGENT_NAME
    assert len(calls) == 4


def test_fixed_ontology_and_shapes_can_be_validated() -> None:
    """Fixed ontology and SHACL Turtle can be parsed before agent execution."""

    validation = validate_ontology_and_shapes(ONTOLOGY_TTL, SHAPES_TTL)

    assert validation.conforms
    assert validation.ontology_hash == content_hash(ONTOLOGY_TTL)
    assert validation.shapes_hash == content_hash(SHAPES_TTL)


def test_workflow_agent_output_keeps_explanatory_fields_flexible() -> None:
    """Design and explanatory outputs may use evolving JSON structures."""

    output = WorkflowAgentOutput(
        mode=WorkflowAgentMode.WORKFLOW_GENERATION,
        workflow_rdf_turtle=VALID_WORKFLOW_TTL,
        used_vocabulary_terms={
            "standard": ["rdf:type"],
            "provisional": ["wf:Activity"],
        },
        generation_notes={
            "policy": "no inferred triples",
            "warnings": [{"step_id": "S1", "message": "actor is uncertain"}],
        },
        unresolved_items={"by_step": {"S1": ["actor identity is uncertain"]}},
    )

    dumped = output.model_dump(mode="json")

    assert dumped["generation_notes"]["warnings"][0]["step_id"] == "S1"


def test_workflow_agent_recovers_duplicate_key_output(monkeypatch) -> None:
    """Recover first non-null Turtle when SDK validation error includes duplicate keys."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_runner(_agent, _prompt):
        raise ModelBehaviorError(
            'Invalid JSON when parsing {"mode":"workflow_generation",'
            f'"workflow_rdf_turtle":{json.dumps(VALID_WORKFLOW_TTL)},'
            '"workflow_rdf_turtle":null} for TypeAdapter(WorkflowAgentOutput)'
        )

    output = run_workflow_agent(
        WorkflowAgentMode.WORKFLOW_GENERATION,
        payload={"scenario_rdf_turtle": _scenario_turtle()},
        model="gpt-test",
        runner=fake_runner,
    )

    assert output.workflow_rdf_turtle == VALID_WORKFLOW_TTL


@pytest.mark.parametrize(
    ("mode", "expected_message"),
    [
        (WorkflowAgentMode.WORKFLOW_GENERATION, "workflow_rdf_turtle"),
        (WorkflowAgentMode.WORKFLOW_REVISION, "workflow_rdf_turtle"),
    ],
)
def test_workflow_agent_output_allows_missing_turtle_until_pipeline_check(
    mode: WorkflowAgentMode,
    expected_message: str,
) -> None:
    """SDK parsing should not fail before the pipeline can report missing Turtle."""

    output = WorkflowAgentOutput(mode=mode)

    assert output.mode == mode
    assert expected_message.endswith("_turtle")


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


def test_new_class_is_detected_even_in_an_instance_namespace() -> None:
    """An AI-created class is not mistaken for a permitted business instance URI."""

    workflow = VALID_WORKFLOW_TTL + "\ninst:new-item a inst:EligibilityCheckTask ."
    validation = validate_workflow_rdf(workflow, ONTOLOGY_TTL, SHAPES_TTL)

    assert not validation.conforms
    assert "http://example.org/workflow#id/EligibilityCheckTask" in (
        validation.vocabulary.unauthorized_terms
    )


def test_shacl_violations_are_detected() -> None:
    """pySHACL violations are exposed in Workflow RDF validation."""

    validation = validate_workflow_rdf(INVALID_WORKFLOW_TTL, ONTOLOGY_TTL, SHAPES_TTL)

    assert not validation.conforms
    assert validation.shacl_result is not None
    assert validation.shacl_result.violations


def _write_fixed_files(tmp_path):
    ontology_file = tmp_path / "workflow_ontology.ttl"
    shapes_file = tmp_path / "workflow_shapes.ttl"
    ontology_file.write_text(ONTOLOGY_TTL, encoding="utf-8")
    shapes_file.write_text(SHAPES_TTL, encoding="utf-8")
    return ontology_file, shapes_file


def _write_source_pdf(tmp_path):
    pdf_file = tmp_path / "source.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "PDF detail: a clerk checks the order amount.")
    document.save(pdf_file)
    document.close()
    return pdf_file


def test_pipeline_stops_when_conforms_true(monkeypatch, tmp_path) -> None:
    """Pipeline saves final artifacts and stops without revision when SHACL conforms."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    ontology_file, _ = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)
    workflow_dir = tmp_path / "workflow"
    workflow_dir.mkdir()
    (workflow_dir / "workflow_run_metadata.json").write_text("stale", encoding="utf-8")
    progress_stream = StringIO()
    progress = ProgressReporter(stream=progress_stream)

    call_order: list[str] = []

    def fake_runner(_agent, prompt):
        if "workflow_shacl_generation" in prompt:
            call_order.append("workflow_shacl_generation")
            assert "workflow_rdf_raw" in prompt
            assert "inst:activity-S1" in prompt
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SHACL_GENERATION,
                    workflow_shacl_turtle=SHAPES_TTL,
                )
            )
        if "workflow_self_review" in prompt:
            call_order.append("workflow_self_review")
            assert "current_workflow_rdf" in prompt
            assert "PDF detail: a clerk checks the order amount." in prompt
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SELF_REVIEW,
                    self_review_result=_workflow_review(),
                )
            )
        call_order.append("workflow_generation")
        assert "source_document" in prompt
        assert "PDF detail: a clerk checks the order amount." in prompt
        assert "scenario_rdf_turtle" in prompt
        assert "Order handling" in prompt
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                workflow_rdf_turtle=VALID_WORKFLOW_TTL,
            )
        )

    result = run_workflow_pipeline(
        scenario_file,
        model="gpt-test",
        pdf_file=pdf_file,
        output_dir=workflow_dir,
        runner=fake_runner,
        ontology_file=ontology_file,
        progress=progress,
    )

    assert call_order == [
        "workflow_generation",
        "workflow_shacl_generation",
        "workflow_self_review",
    ]
    assert result["final_status"] == "completed"
    progress_output = progress_stream.getvalue()
    assert "[RUN] Workflow RDF generation" in progress_output
    assert "[RUN] Workflow SHACL generation" in progress_output
    assert "[OK] Workflow Self-Review passed" in progress_output
    assert {path.name for path in workflow_dir.iterdir()} == {
        "workflow_final.ttl",
        "workflow_shapes_generated.ttl",
        "workflow_validation.json",
        "workflow_revision_history.json",
        "workflow_self_review.json",
    }
    assert json.loads(
        (workflow_dir / "workflow_validation.json").read_text(encoding="utf-8")
    )["conforms"]
    assert json.loads(
        (workflow_dir / "workflow_revision_history.json").read_text(encoding="utf-8")
    ) == []
    assert json.loads(
        (workflow_dir / "workflow_self_review.json").read_text(encoding="utf-8")
    )["status"] == "passed"


def test_pipeline_saves_generated_shacl_before_rejecting_invalid_turtle(
    monkeypatch,
    tmp_path,
) -> None:
    """Malformed AI-generated SHACL is preserved and stops RDF validation."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    ontology_file, _ = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)
    output_dir = tmp_path / "workflow"
    invalid_shapes = "@prefix sh: <http://www.w3.org/ns/shacl#> . ["

    def fake_runner(_agent, prompt):
        if "workflow_shacl_generation" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SHACL_GENERATION,
                    workflow_shacl_turtle=invalid_shapes,
                )
            )
        if "workflow_self_review" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SELF_REVIEW,
                    self_review_result=_workflow_review(),
                )
            )
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                workflow_rdf_turtle=VALID_WORKFLOW_TTL,
            )
        )

    with pytest.raises(ValueError, match="Generated Workflow SHACL"):
        run_workflow_pipeline(
            scenario_file,
            model="gpt-test",
            pdf_file=pdf_file,
            output_dir=output_dir,
            runner=fake_runner,
            ontology_file=ontology_file,
        )

    assert (output_dir / "workflow_shapes_generated.ttl").read_text(
        encoding="utf-8"
    ) == invalid_shapes


def test_pipeline_runs_revision_until_max_iterations(monkeypatch, tmp_path) -> None:
    """Pipeline stops at max workflow revisions when violations remain."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    ontology_file, _ = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)
    revision_calls = 0
    shacl_calls = 0

    def fake_runner(_agent, prompt):
        nonlocal revision_calls, shacl_calls
        if "workflow_shacl_generation" in prompt:
            shacl_calls += 1
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SHACL_GENERATION,
                    workflow_shacl_turtle=SHAPES_TTL,
                )
            )
        assert "source_document" in prompt
        assert "PDF detail: a clerk checks the order amount." in prompt
        assert "scenario_rdf_turtle" in prompt
        assert "Order handling" in prompt
        if "workflow_revision" in prompt:
            revision_calls += 1
            assert "wf:ActivityShape" in prompt
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
        pdf_file=pdf_file,
        output_dir=tmp_path / "workflow",
        runner=fake_runner,
        ontology_file=ontology_file,
    )

    assert result["final_status"] == "needs_review"
    assert revision_calls == MAX_REVISION_ITERATIONS
    assert shacl_calls == 1
    workflow_dir = tmp_path / "workflow"
    history = json.loads(
        (workflow_dir / "workflow_revision_history.json").read_text(encoding="utf-8")
    )
    assert [entry["iteration"] for entry in history] == list(
        range(1, MAX_REVISION_ITERATIONS + 1)
    )
    assert all("output" in entry and "validation" in entry for entry in history)
    assert (workflow_dir / "workflow_final.ttl").read_text(
        encoding="utf-8"
    ) == INVALID_WORKFLOW_TTL
    assert not json.loads(
        (workflow_dir / "workflow_validation.json").read_text(encoding="utf-8")
    )["conforms"]
    assert json.loads(
        (workflow_dir / "workflow_self_review.json").read_text(encoding="utf-8")
    )["status"] == "shacl_failed"


def test_pipeline_saves_detailed_artifacts_in_debug_mode(monkeypatch, tmp_path) -> None:
    """Debug mode preserves the detailed artifacts omitted from normal runs."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    ontology_file, _ = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)

    def fake_runner(_agent, prompt):
        if "workflow_shacl_generation" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SHACL_GENERATION,
                    workflow_shacl_turtle=SHAPES_TTL,
                )
            )
        if "workflow_self_review" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SELF_REVIEW,
                    self_review_result=_workflow_review(),
                )
            )
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                workflow_rdf_turtle=VALID_WORKFLOW_TTL,
            )
        )

    run_workflow_pipeline(
        scenario_file,
        model="gpt-test",
        pdf_file=pdf_file,
        output_dir=tmp_path / "workflow",
        runner=fake_runner,
        ontology_file=ontology_file,
        save_debug_outputs=True,
    )

    workflow_dir = tmp_path / "workflow"
    assert (workflow_dir / "workflow_agent_output.json").exists()
    assert (workflow_dir / "workflow_run_metadata.json").exists()
    assert (workflow_dir / "workflow_ontology_history.json").exists()
    assert (workflow_dir / "workflow_ontology_v0_1.ttl").exists()
    assert (workflow_dir / "workflow_shapes_generated.ttl").exists()


def test_self_review_revises_rdf_and_reuses_generated_shacl(
    monkeypatch,
    tmp_path,
) -> None:
    """A semantic finding revises only RDF before SHACL and Self-Review rerun."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    ontology_file, _ = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)
    revised_turtle = VALID_WORKFLOW_TTL.replace("Check order", "Check order amount")
    shacl_calls = 0
    review_calls = 0
    revision_calls = 0

    finding = SelfReviewFinding(
        category=SelfReviewCategory.MISSING_INFORMATION,
        target="inst:activity-S1",
        description="The amount check is missing from the step name.",
        evidence=[
            SelfReviewEvidence(
                source="pdf",
                locator="page 1",
                excerpt="a clerk checks the order amount",
            )
        ],
        revision_instruction="Include the documented amount check.",
    )

    def fake_runner(_agent, prompt):
        nonlocal shacl_calls, review_calls, revision_calls
        if "workflow_shacl_generation" in prompt:
            shacl_calls += 1
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SHACL_GENERATION,
                    workflow_shacl_turtle=SHAPES_TTL,
                )
            )
        if "workflow_self_review" in prompt:
            review_calls += 1
            assert "PDF detail: a clerk checks the order amount." in prompt
            assert "Order handling" in prompt
            assert "current_workflow_rdf" in prompt
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SELF_REVIEW,
                    self_review_result=(
                        _workflow_review(False, [finding])
                        if review_calls == 1
                        else _workflow_review()
                    ),
                )
            )
        if "workflow_revision" in prompt:
            revision_calls += 1
            assert "self_review_result" in prompt
            assert "Include the documented amount check." in prompt
            assert "wf:ActivityShape" in prompt
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_REVISION,
                    workflow_rdf_turtle=revised_turtle,
                )
            )
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                workflow_rdf_turtle=VALID_WORKFLOW_TTL,
            )
        )

    output_dir = tmp_path / "workflow"
    result = run_workflow_pipeline(
        scenario_file,
        model="gpt-test",
        pdf_file=pdf_file,
        output_dir=output_dir,
        runner=fake_runner,
        ontology_file=ontology_file,
    )

    assert result["final_status"] == "completed"
    assert shacl_calls == 1
    assert review_calls == 2
    assert revision_calls == 1
    assert (output_dir / "workflow_final.ttl").read_text(
        encoding="utf-8"
    ) == revised_turtle
    review_history = json.loads(
        (output_dir / "workflow_self_review.json").read_text(encoding="utf-8")
    )
    assert review_history["status"] == "passed"
    assert len(review_history["iterations"]) == 2
    revision_history = json.loads(
        (output_dir / "workflow_revision_history.json").read_text(encoding="utf-8")
    )
    assert revision_history[0]["phase"] == "self_review_revision"


def test_self_review_stops_at_shared_revision_limit(monkeypatch, tmp_path) -> None:
    """An unresolved semantic finding ends as needs_review without an infinite loop."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    ontology_file, _ = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)
    finding = SelfReviewFinding(
        category=SelfReviewCategory.MISSING_INFORMATION,
        description="A documented workflow detail is missing.",
        evidence=[
            SelfReviewEvidence(
                source="pdf",
                locator="page 1",
                excerpt="checks the order amount",
            )
        ],
        revision_instruction="Add the documented workflow detail.",
    )
    review_calls = 0
    revision_calls = 0

    def fake_runner(_agent, prompt):
        nonlocal review_calls, revision_calls
        if "workflow_shacl_generation" in prompt:
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SHACL_GENERATION,
                    workflow_shacl_turtle=SHAPES_TTL,
                )
            )
        if "workflow_self_review" in prompt:
            review_calls += 1
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SELF_REVIEW,
                    self_review_result=_workflow_review(False, [finding]),
                )
            )
        if "workflow_revision" in prompt:
            revision_calls += 1
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_REVISION,
                    workflow_rdf_turtle=VALID_WORKFLOW_TTL,
                )
            )
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                workflow_rdf_turtle=VALID_WORKFLOW_TTL,
            )
        )

    output_dir = tmp_path / "workflow"
    result = run_workflow_pipeline(
        scenario_file,
        model="gpt-test",
        pdf_file=pdf_file,
        output_dir=output_dir,
        runner=fake_runner,
        ontology_file=ontology_file,
    )

    assert result["final_status"] == "needs_review"
    history = json.loads(
        (output_dir / "workflow_self_review.json").read_text(encoding="utf-8")
    )
    assert history["status"] == "max_iterations"
    assert history["max_revision_iterations"] == MAX_REVISION_ITERATIONS
    assert revision_calls == MAX_REVISION_ITERATIONS
    assert review_calls == MAX_REVISION_ITERATIONS + 1
    assert len(history["iterations"]) == MAX_REVISION_ITERATIONS + 1


def test_pipeline_does_not_autofill_business_triples(monkeypatch, tmp_path) -> None:
    """Python pipeline does not create business RDF triples by itself."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    ontology_file, _ = _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)
    generated = "this is not turtle"

    def fake_runner(_agent, prompt):
        if "workflow_shacl_generation" in prompt:
            assert generated in prompt
            return SimpleNamespace(
                final_output=WorkflowAgentOutput(
                    mode=WorkflowAgentMode.WORKFLOW_SHACL_GENERATION,
                    workflow_shacl_turtle=SHAPES_TTL,
                )
            )
        assert "PDF detail: a clerk checks the order amount." in prompt
        return SimpleNamespace(
            final_output=WorkflowAgentOutput(
                mode=WorkflowAgentMode.WORKFLOW_GENERATION,
                workflow_rdf_turtle=generated,
            )
        )

    run_workflow_pipeline(
        scenario_file,
        model="gpt-test",
        pdf_file=pdf_file,
        output_dir=tmp_path / "workflow",
        runner=fake_runner,
        ontology_file=ontology_file,
    )

    assert (tmp_path / "workflow" / "workflow_final.ttl").read_text(
        encoding="utf-8"
    ) == generated


def test_pipeline_stops_when_fixed_ontology_is_missing(monkeypatch, tmp_path) -> None:
    """A missing fixed TTL fails clearly without invoking the AI fallback."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scenario_file = tmp_path / "scenario_final.ttl"
    scenario_file.write_text(_scenario_turtle(), encoding="utf-8")
    _write_fixed_files(tmp_path)
    pdf_file = _write_source_pdf(tmp_path)

    def unexpected_runner(_agent, _prompt):
        pytest.fail("Agent must not run when a fixed ontology file is missing")

    with pytest.raises(FileNotFoundError, match="Required Workflow ontology"):
        run_workflow_pipeline(
            scenario_file,
            model="gpt-test",
            pdf_file=pdf_file,
            output_dir=tmp_path / "workflow",
            runner=unexpected_runner,
            ontology_file=tmp_path / "missing.ttl",
        )
