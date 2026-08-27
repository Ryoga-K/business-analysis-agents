"""Startup tests for the prototype."""

from pathlib import Path

from business_analysis_agents.controller import run
from business_analysis_agents.models import ScenarioAgentOutput, SourceDocument


def test_run_prints_startup_message(capsys) -> None:
    """The current prototype should start with a clear CLI message."""

    run()
    captured = capsys.readouterr()
    assert "システムを開始しました" in captured.out


def test_run_generates_scenario_rdf_in_fixed_output_directory(
    monkeypatch,
    tmp_path,
) -> None:
    """The Scenario command passes the fixed ontology and saves Turtle output."""

    ontology_turtle = "@prefix prov: <http://www.w3.org/ns/prov#> ."
    scenario_turtle = "<urn:business> <http://purl.org/dc/terms/title> \"Business\" ."
    document = SourceDocument(document_id="doc-1", title="manual", text="content")

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "business_analysis_agents.controller.load_pdf_document",
        lambda path: document,
    )
    monkeypatch.setattr(
        "business_analysis_agents.controller.load_fixed_turtle",
        lambda path, label: (Path(path), ontology_turtle),
    )

    def fake_run_scenario_agent(agent_input, model):
        assert agent_input.document == document
        assert agent_input.ontology_turtle == ontology_turtle
        return ScenarioAgentOutput(scenario_rdf_turtle=scenario_turtle)

    monkeypatch.setattr(
        "business_analysis_agents.controller.run_scenario_agent",
        fake_run_scenario_agent,
    )

    assert run(["manual.pdf"]) == 0
    output_path = tmp_path / "scenario" / "scenario_final.ttl"
    assert output_path.read_text(encoding="utf-8") == scenario_turtle
