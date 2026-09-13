"""Tests for Cross-SHACL generation and RDF consistency evaluation."""

from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace

import pytest

from business_analysis_agents.agents.consistency import (
    CONSISTENCY_AGENT_NAME,
    build_consistency_agent,
    run_consistency_agent,
)
from business_analysis_agents.consistency_pipeline import run_consistency_pipeline
from business_analysis_agents.models import (
    AgentName,
    ConsistencyAgentMode,
    ConsistencyAgentOutput,
    ConsistencyViolationAnalysis,
    RdfKind,
)
from business_analysis_agents.rdf_validation import content_hash, validate_cross_rdf
from business_analysis_agents.progress import ProgressReporter


WORKFLOW_ONTOLOGY = """
@prefix wf: <http://example.org/workflow#> .
@prefix data: <http://example.org/data#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
wf:Activity a rdfs:Class .
wf:usesData a rdf:Property ; rdfs:domain wf:Activity ; rdfs:range data:DataEntity .
""".strip()

DATA_ONTOLOGY = """
@prefix data: <http://example.org/data#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
data:DataEntity a rdfs:Class .
""".strip()

RULE_ONTOLOGY = """
@prefix rule: <http://example.org/rule#> .
@prefix wf: <http://example.org/workflow#> .
@prefix data: <http://example.org/data#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
rule:Rule a rdfs:Class .
rule:governsActivity a rdf:Property ; rdfs:domain rule:Rule ; rdfs:range wf:Activity .
rule:usesData a rdf:Property ; rdfs:domain rule:Rule ; rdfs:range data:DataEntity .
""".strip()

WORKFLOW_RDF = """
@prefix wf: <http://example.org/workflow#> .
@prefix inst: <http://example.org/instance/> .
inst:activity a wf:Activity ; wf:usesData inst:document .
""".strip()

INVALID_WORKFLOW_RDF = """
@prefix wf: <http://example.org/workflow#> .
@prefix inst: <http://example.org/instance/> .
inst:activity a wf:Activity ; wf:usesData inst:missing-document .
""".strip()

DATA_RDF = """
@prefix data: <http://example.org/data#> .
@prefix inst: <http://example.org/instance/> .
inst:document a data:DataEntity .
""".strip()

RULE_RDF = """
@prefix rule: <http://example.org/rule#> .
@prefix inst: <http://example.org/instance/> .
inst:rule a rule:Rule ;
    rule:governsActivity inst:activity ;
    rule:usesData inst:document .
""".strip()

CROSS_SHAPES = """
@prefix cross: <http://example.org/cross-shapes#> .
@prefix wf: <http://example.org/workflow#> .
@prefix data: <http://example.org/data#> .
@prefix rule: <http://example.org/rule#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .

cross:WorkflowDataReferenceShape a sh:NodeShape ;
    sh:targetClass wf:Activity ;
    sh:property [ sh:path wf:usesData ; sh:class data:DataEntity ] .

cross:RuleReferenceShape a sh:NodeShape ;
    sh:targetClass rule:Rule ;
    sh:property [ sh:path rule:governsActivity ; sh:class wf:Activity ] ;
    sh:property [ sh:path rule:usesData ; sh:class data:DataEntity ] .
""".strip()

WORKFLOW_DATA_CROSS_SHAPES = """
@prefix cross: <http://example.org/cross-shapes#> .
@prefix wf: <http://example.org/workflow#> .
@prefix data: <http://example.org/data#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .

cross:WorkflowDataReferenceShape a sh:NodeShape ;
    sh:targetClass wf:Activity ;
    sh:property [ sh:path wf:usesData ; sh:class data:DataEntity ] .
""".strip()


def _write_inputs(tmp_path, workflow_rdf: str = WORKFLOW_RDF):
    values = {
        "workflow.ttl": workflow_rdf,
        "data.ttl": DATA_RDF,
        "rule.ttl": RULE_RDF,
        "workflow_ontology.ttl": WORKFLOW_ONTOLOGY,
        "data_ontology.ttl": DATA_ONTOLOGY,
        "rule_ontology.ttl": RULE_ONTOLOGY,
        "workflow_validation.json": '{"conforms": true}',
        "data_validation.json": '{"conforms": true}',
        "rule_validation.json": '{"conforms": true}',
    }
    paths = {}
    for name, text in values.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        paths[name] = path
    return paths


def test_consistency_agent_uses_one_agent_for_both_modes(monkeypatch) -> None:
    """Cross-SHACL generation and violation analysis share one SDK Agent."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_runner(agent, prompt):
        assert agent.name == CONSISTENCY_AGENT_NAME
        assert agent.output_type.output_type is ConsistencyAgentOutput
        if "cross_shacl_generation" in prompt:
            assert "名前や説明が似ている" in agent.instructions
            return SimpleNamespace(
                final_output=ConsistencyAgentOutput(
                    mode=ConsistencyAgentMode.CROSS_SHACL_GENERATION,
                    cross_shacl_turtle=CROSS_SHAPES,
                )
            )
        return SimpleNamespace(
            final_output=ConsistencyAgentOutput(
                mode=ConsistencyAgentMode.VIOLATION_ANALYSIS,
                violation_analyses=[
                    ConsistencyViolationAnalysis(
                        violation_index=0,
                        target_resource="http://example.org/instance/activity",
                        cause="The referenced Data resource has no explicit type.",
                        target_agent=AgentName.DATA,
                        repair_instruction="Review the Data RDF reference.",
                    )
                ],
            )
        )

    for mode in ConsistencyAgentMode:
        output = run_consistency_agent(
            mode,
            payload={"workflow_rdf_turtle": WORKFLOW_RDF},
            model="gpt-test",
            runner=fake_runner,
        )
        assert output.mode is mode

    assert build_consistency_agent("gpt-test").name == CONSISTENCY_AGENT_NAME


def test_validate_cross_rdf_merges_all_three_graphs() -> None:
    """Cross-SHACL can resolve references located in another RDF graph."""

    validation = validate_cross_rdf(
        WORKFLOW_RDF,
        DATA_RDF,
        RULE_RDF,
        WORKFLOW_ONTOLOGY,
        DATA_ONTOLOGY,
        RULE_ONTOLOGY,
        CROSS_SHAPES,
    )

    assert validation.conforms
    assert validation.shacl_result is not None
    assert validation.shacl_result.violations == []


def test_validate_cross_rdf_merges_workflow_and_data_without_rule() -> None:
    """Cross-SHACL validation accepts the E2E Workflow/Data graph pair."""

    validation = validate_cross_rdf(
        WORKFLOW_RDF,
        DATA_RDF,
        None,
        WORKFLOW_ONTOLOGY,
        DATA_ONTOLOGY,
        None,
        CROSS_SHAPES,
    )

    assert validation.conforms
    assert validation.shacl_result is not None
    assert validation.shacl_result.violations == []


def test_consistency_pipeline_does_not_require_rule_inputs(monkeypatch, tmp_path) -> None:
    """The E2E Cross pipeline sends only Workflow/Data RDFs to the Agent."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    paths = _write_inputs(tmp_path)

    def fake_runner(_agent, prompt):
        assert "workflow_rdf_turtle" in prompt
        assert "data_rdf_turtle" in prompt
        assert "rule_rdf_turtle" not in prompt
        assert "修正対象候補: workflow, data" in prompt
        return SimpleNamespace(
            final_output=ConsistencyAgentOutput(
                mode=ConsistencyAgentMode.CROSS_SHACL_GENERATION,
                cross_shacl_turtle=WORKFLOW_DATA_CROSS_SHAPES,
            )
        )

    result = run_consistency_pipeline(
        model="gpt-test",
        workflow_file=paths["workflow.ttl"],
        data_file=paths["data.ttl"],
        rule_file=None,
        workflow_validation_file=paths["workflow_validation.json"],
        data_validation_file=paths["data_validation.json"],
        output_dir=tmp_path / "consistency-data-only",
        workflow_ontology_file=paths["workflow_ontology.ttl"],
        data_ontology_file=paths["data_ontology.ttl"],
        runner=fake_runner,
    )

    assert result["final_status"] == "completed"
    assert "rule_file" not in result
    assert "rule_validation_file" not in result
    assert "rule_ontology_file" not in result


def test_data_only_consistency_rejects_rule_repair_target(
    monkeypatch,
    tmp_path,
) -> None:
    """Violation analysis cannot route an E2E repair to the excluded Rule Agent."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    paths = _write_inputs(tmp_path, workflow_rdf=INVALID_WORKFLOW_RDF)

    def fake_runner(_agent, prompt):
        if "cross_shacl_generation" in prompt:
            return SimpleNamespace(
                final_output=ConsistencyAgentOutput(
                    mode=ConsistencyAgentMode.CROSS_SHACL_GENERATION,
                    cross_shacl_turtle=WORKFLOW_DATA_CROSS_SHAPES,
                )
            )
        return SimpleNamespace(
            final_output=ConsistencyAgentOutput(
                mode=ConsistencyAgentMode.VIOLATION_ANALYSIS,
                violation_analyses=[
                    ConsistencyViolationAnalysis(
                        violation_index=0,
                        target_resource="http://example.org/instance/missing-document",
                        cause="The Workflow reference is missing from Data RDF.",
                        target_agent=AgentName.RULE,
                        repair_instruction="Do not route this to Rule.",
                    )
                ],
            )
        )

    with pytest.raises(ValueError, match="unsupported repair targets.*rule"):
        run_consistency_pipeline(
            model="gpt-test",
            workflow_file=paths["workflow.ttl"],
            data_file=paths["data.ttl"],
            rule_file=None,
            workflow_validation_file=paths["workflow_validation.json"],
            data_validation_file=paths["data_validation.json"],
            output_dir=tmp_path / "invalid-rule-target",
            workflow_ontology_file=paths["workflow_ontology.ttl"],
            data_ontology_file=paths["data_ontology.ttl"],
            runner=fake_runner,
        )


def test_consistency_pipeline_saves_shapes_and_conforming_result(
    monkeypatch,
    tmp_path,
) -> None:
    """A conforming run generates Cross-SHACL once and saves its result."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    paths = _write_inputs(tmp_path)
    calls: list[str] = []
    progress_stream = StringIO()
    progress = ProgressReporter(stream=progress_stream)

    def fake_runner(_agent, prompt):
        calls.append(prompt)
        assert "workflow_rdf_turtle" in prompt
        assert "data_rdf_turtle" in prompt
        assert "rule_rdf_turtle" in prompt
        return SimpleNamespace(
            final_output=ConsistencyAgentOutput(
                mode=ConsistencyAgentMode.CROSS_SHACL_GENERATION,
                cross_shacl_turtle=CROSS_SHAPES,
            )
        )

    output_dir = tmp_path / "consistency"
    result = run_consistency_pipeline(
        model="gpt-test",
        workflow_file=paths["workflow.ttl"],
        data_file=paths["data.ttl"],
        rule_file=paths["rule.ttl"],
        workflow_validation_file=paths["workflow_validation.json"],
        data_validation_file=paths["data_validation.json"],
        rule_validation_file=paths["rule_validation.json"],
        workflow_ontology_file=paths["workflow_ontology.ttl"],
        data_ontology_file=paths["data_ontology.ttl"],
        rule_ontology_file=paths["rule_ontology.ttl"],
        output_dir=output_dir,
        runner=fake_runner,
        progress=progress,
    )

    assert len(calls) == 1
    assert result["final_status"] == "completed"
    progress_output = progress_stream.getvalue()
    assert "[RUN] Cross-SHACL generation" in progress_output
    assert "[RUN] Cross validation" in progress_output
    assert "[OK] Cross validation passed" in progress_output
    assert {path.name for path in output_dir.iterdir()} == {
        "consistency_shapes_generated.ttl",
        "consistency_validation.json",
        "consistency_evaluation.json",
    }
    evaluation = json.loads(
        (output_dir / "consistency_evaluation.json").read_text(encoding="utf-8")
    )
    assert evaluation["conforms"]
    assert evaluation["violation_analyses"] == []


def test_consistency_pipeline_reuses_existing_cross_shacl(
    monkeypatch,
    tmp_path,
) -> None:
    """A repeated Cross Review validates with the original generated shapes."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    paths = _write_inputs(tmp_path)
    output_dir = tmp_path / "consistency"
    shapes_file = output_dir / "consistency_shapes_generated.ttl"
    shapes_file.parent.mkdir()
    shapes_file.write_text(CROSS_SHAPES, encoding="utf-8")
    expected_hash = content_hash(CROSS_SHAPES)

    def unexpected_runner(_agent, _prompt):
        pytest.fail("No agent call is needed when reused Cross-SHACL conforms")

    result = run_consistency_pipeline(
        model="gpt-test",
        workflow_file=paths["workflow.ttl"],
        data_file=paths["data.ttl"],
        rule_file=paths["rule.ttl"],
        workflow_validation_file=paths["workflow_validation.json"],
        data_validation_file=paths["data_validation.json"],
        rule_validation_file=paths["rule_validation.json"],
        workflow_ontology_file=paths["workflow_ontology.ttl"],
        data_ontology_file=paths["data_ontology.ttl"],
        rule_ontology_file=paths["rule_ontology.ttl"],
        output_dir=output_dir,
        runner=unexpected_runner,
        cross_shacl_file=shapes_file,
        expected_cross_shapes_hash=expected_hash,
    )

    assert result["final_status"] == "completed"
    assert result["cross_shacl_source"] == "reused"
    assert result["cross_shapes_hash"] == expected_hash
    assert shapes_file.read_text(encoding="utf-8") == CROSS_SHAPES


def test_consistency_pipeline_accepts_usable_rdf_with_individual_issues(
    monkeypatch,
    tmp_path,
) -> None:
    """Individual quality issues do not block Cross Review for parseable RDF."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    paths = _write_inputs(tmp_path)
    paths["data_validation.json"].write_text(
        '{"conforms": false}',
        encoding="utf-8",
    )

    calls = []

    def fake_runner(_agent, prompt):
        calls.append(prompt)
        return SimpleNamespace(
            final_output=ConsistencyAgentOutput(
                mode=ConsistencyAgentMode.CROSS_SHACL_GENERATION,
                cross_shacl_turtle=CROSS_SHAPES,
            )
        )

    result = run_consistency_pipeline(
        model="gpt-test",
        workflow_file=paths["workflow.ttl"],
        data_file=paths["data.ttl"],
        rule_file=paths["rule.ttl"],
        workflow_validation_file=paths["workflow_validation.json"],
        data_validation_file=paths["data_validation.json"],
        rule_validation_file=paths["rule_validation.json"],
        workflow_ontology_file=paths["workflow_ontology.ttl"],
        data_ontology_file=paths["data_ontology.ttl"],
        rule_ontology_file=paths["rule_ontology.ttl"],
        output_dir=tmp_path / "consistency",
        runner=fake_runner,
    )

    assert result["final_status"] == "completed"
    assert len(calls) == 1


def test_consistency_pipeline_analyzes_violation_without_rerunning_agents(
    monkeypatch,
    tmp_path,
) -> None:
    """A Cross-SHACL violation produces repair guidance but no repair loop."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    paths = _write_inputs(tmp_path, workflow_rdf=INVALID_WORKFLOW_RDF)
    calls: list[str] = []

    def fake_runner(_agent, prompt):
        calls.append(prompt)
        if "cross_shacl_generation" in prompt:
            return SimpleNamespace(
                final_output=ConsistencyAgentOutput(
                    mode=ConsistencyAgentMode.CROSS_SHACL_GENERATION,
                    cross_shacl_turtle=CROSS_SHAPES,
                )
            )
        assert "violation_analysis" in prompt
        assert "missing-document" in prompt
        return SimpleNamespace(
            final_output=ConsistencyAgentOutput(
                mode=ConsistencyAgentMode.VIOLATION_ANALYSIS,
                violation_analyses=[
                    ConsistencyViolationAnalysis(
                        violation_index=0,
                        target_resource="http://example.org/instance/activity",
                        cause="Workflow references a Data resource absent from Data RDF.",
                        target_agent=AgentName.DATA,
                        repair_instruction=(
                            "Confirm the source evidence and add or correct the referenced "
                            "Data RDF resource without changing the ontology."
                        ),
                    )
                ],
                summary="One Workflow-to-Data reference is inconsistent.",
            )
        )

    output_dir = tmp_path / "consistency"
    result = run_consistency_pipeline(
        model="gpt-test",
        workflow_file=paths["workflow.ttl"],
        data_file=paths["data.ttl"],
        rule_file=paths["rule.ttl"],
        workflow_validation_file=paths["workflow_validation.json"],
        data_validation_file=paths["data_validation.json"],
        rule_validation_file=paths["rule_validation.json"],
        workflow_ontology_file=paths["workflow_ontology.ttl"],
        data_ontology_file=paths["data_ontology.ttl"],
        rule_ontology_file=paths["rule_ontology.ttl"],
        output_dir=output_dir,
        runner=fake_runner,
    )

    assert len(calls) == 2
    assert result["final_status"] == "needs_revision"
    evaluation = result["evaluation"]
    assert not evaluation["conforms"]
    assert evaluation["target_agent"] == "data"
    assert evaluation["violations"][0]["rdf_kind"] == RdfKind.CONSISTENCY.value
    assert evaluation["violations"][0]["constraint_component"].endswith(
        "ClassConstraintComponent"
    )
    assert evaluation["violation_analyses"][0]["repair_instruction"]
