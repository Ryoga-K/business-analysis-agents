"""Pipeline for generating Workflow RDF from scenario JSON."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from business_analysis_agents.agents.workflow import run_workflow_agent
from business_analysis_agents.fixed_resources import (
    DEFAULT_WORKFLOW_ONTOLOGY,
    DEFAULT_WORKFLOW_SHAPES,
    load_fixed_turtle,
)
from business_analysis_agents.models import (
    OntologyValidationResult,
    ScenarioAgentOutput,
    WorkflowAgentMode,
    WorkflowAgentOutput,
    WorkflowRdfValidationResult,
)
from business_analysis_agents.rdf_validation import (
    content_hash,
    validate_ontology_and_shapes,
    validate_workflow_rdf,
    validation_feedback,
)


DEFAULT_MAX_WORKFLOW_ITERATIONS = 3


def load_scenario_output(path: Path | str) -> ScenarioAgentOutput:
    """Load ScenarioAgentOutput JSON from disk."""

    scenario_path = Path(path)
    if scenario_path.is_dir():
        scenario_path = scenario_path / "scenario.json"
    if not scenario_path.exists():
        raise FileNotFoundError(
            f"Scenario JSON was not found: {scenario_path}. "
            "Pass the path to scenario.json, for example "
            "--scenario outputs/run_YYYYMMDD_HHMMSS/scenario.json"
        )
    return ScenarioAgentOutput.model_validate_json(
        scenario_path.read_text(encoding="utf-8")
    )


def write_json(path: Path | str, data: Any) -> None:
    """Write JSON data with UTF-8 encoding."""

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def write_text(path: Path | str, text: str) -> None:
    """Write text with UTF-8 encoding."""

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def _output_payload(output: WorkflowAgentOutput) -> dict[str, Any]:
    return output.model_dump(mode="json")


def _validation_payload(validation: WorkflowRdfValidationResult) -> dict[str, Any]:
    data = validation.model_dump(mode="json")
    data["conforms"] = validation.conforms
    return data


def _ontology_validation_payload(validation: OntologyValidationResult) -> dict[str, Any]:
    data = validation.model_dump(mode="json")
    data["conforms"] = validation.conforms
    return data


def _require_text(value: str | None, field_name: str) -> str:
    if not value:
        raise ValueError(f"Workflow Agent output did not include {field_name}.")
    return value


def _assert_fixed_hashes(
    ontology_turtle: str,
    shacl_turtle: str,
    ontology_hash: str,
    shapes_hash: str,
) -> None:
    if content_hash(ontology_turtle) != ontology_hash:
        raise ValueError("Ontology changed after setup. Workflow revision must not change it.")
    if content_hash(shacl_turtle) != shapes_hash:
        raise ValueError("SHACL shapes changed after setup. Workflow revision must not change them.")


def run_workflow_pipeline(
    scenario_file: Path | str,
    model: str,
    output_dir: Path | str = "outputs/workflow",
    max_workflow_iterations: int = DEFAULT_MAX_WORKFLOW_ITERATIONS,
    runner: Callable[..., Any] | None = None,
    ontology_file: Path | str = DEFAULT_WORKFLOW_ONTOLOGY,
    shapes_file: Path | str = DEFAULT_WORKFLOW_SHAPES,
) -> dict[str, Any]:
    """Run the Workflow RDF generation pipeline and save all artifacts."""

    scenario_path = Path(scenario_file)
    workflow_dir = Path(output_dir)
    workflow_dir.mkdir(parents=True, exist_ok=True)
    scenario = load_scenario_output(scenario_path)
    revision_history: list[dict[str, Any]] = []

    ontology_path, ontology_turtle = load_fixed_turtle(
        ontology_file, "Workflow ontology"
    )
    shapes_path, shacl_turtle = load_fixed_turtle(shapes_file, "Workflow SHACL")
    ontology_validation = validate_ontology_and_shapes(ontology_turtle, shacl_turtle)
    if not ontology_validation.conforms:
        raise ValueError(
            "Fixed Workflow ontology/SHACL validation failed: "
            f"{_ontology_validation_payload(ontology_validation)}"
        )

    ontology_hash = content_hash(ontology_turtle)
    shapes_hash = content_hash(shacl_turtle)

    write_text(workflow_dir / "workflow_ontology_v0_1.ttl", ontology_turtle)
    write_text(workflow_dir / "workflow_shapes_v0_1.ttl", shacl_turtle)
    write_json(
        workflow_dir / "workflow_ontology_design.json",
        {
            "source": "fixed_ttl",
            "ontology_file": str(ontology_path),
            "shapes_file": str(shapes_path),
        },
    )
    write_json(
        workflow_dir / "workflow_ontology_validation.json",
        _ontology_validation_payload(ontology_validation),
    )
    write_json(
        workflow_dir / "workflow_ontology_history.json",
        {
            "fixed": True,
            "source": "fixed_ttl",
            "ontology_hash": ontology_hash,
            "shapes_hash": shapes_hash,
        },
    )

    generation_output = run_workflow_agent(
        WorkflowAgentMode.WORKFLOW_GENERATION,
        {
            "scenario_json": scenario.model_dump(mode="json"),
            "ontology_turtle": ontology_turtle,
            "shacl_turtle": shacl_turtle,
            "ontology_hash": ontology_hash,
            "shapes_hash": shapes_hash,
        },
        model=model,
        runner=runner,
    )
    workflow_turtle = _require_text(
        generation_output.workflow_rdf_turtle,
        "workflow_rdf_turtle",
    )
    validation = validate_workflow_rdf(
        workflow_turtle,
        ontology_turtle,
        shacl_turtle,
        iteration=0,
    )
    final_output = generation_output

    for iteration in range(1, max_workflow_iterations + 1):
        if validation.conforms:
            break

        _assert_fixed_hashes(ontology_turtle, shacl_turtle, ontology_hash, shapes_hash)
        revision_output = run_workflow_agent(
            WorkflowAgentMode.WORKFLOW_REVISION,
            {
                "scenario_json": scenario.model_dump(mode="json"),
                "ontology_turtle": ontology_turtle,
                "shacl_turtle": shacl_turtle,
                "previous_workflow_rdf": workflow_turtle,
                "validation_feedback": validation_feedback(validation),
                "previous_validation": _validation_payload(validation),
                "revision_history": revision_history,
                "ontology_hash": ontology_hash,
                "shapes_hash": shapes_hash,
            },
            model=model,
            runner=runner,
        )
        workflow_turtle = _require_text(
            revision_output.workflow_rdf_turtle,
            "workflow_rdf_turtle",
        )
        validation = validate_workflow_rdf(
            workflow_turtle,
            ontology_turtle,
            shacl_turtle,
            iteration=iteration,
        )
        final_output = revision_output
        revision_history.append(
            {
                "iteration": iteration,
                "output": _output_payload(revision_output),
                "validation": _validation_payload(validation),
            }
        )

    final_status = "completed" if validation.conforms else "needs_review"
    write_text(workflow_dir / "workflow_final.ttl", workflow_turtle)
    write_json(workflow_dir / "workflow_agent_output.json", _output_payload(final_output))
    write_json(workflow_dir / "workflow_validation.json", _validation_payload(validation))
    write_json(workflow_dir / "workflow_revision_history.json", revision_history)

    metadata = {
        "scenario_file": str(scenario_path),
        "execution_datetime": datetime.now().isoformat(),
        "model": model,
        "agent_name": "workflow_agent",
        "ontology_source_file": str(ontology_path),
        "shapes_source_file": str(shapes_path),
        "ontology_hash": ontology_hash,
        "shapes_hash": shapes_hash,
        "max_workflow_iterations": max_workflow_iterations,
        "final_status": final_status,
    }
    write_json(workflow_dir / "workflow_run_metadata.json", metadata)

    return {
        "output_dir": str(workflow_dir),
        "final_status": final_status,
        "ontology_validation": _ontology_validation_payload(ontology_validation),
        "workflow_validation": _validation_payload(validation),
        "metadata": metadata,
    }
