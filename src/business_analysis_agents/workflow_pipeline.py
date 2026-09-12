"""Pipeline for generating and validating Workflow RDF."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from business_analysis_agents.agents.workflow import run_workflow_agent
from business_analysis_agents.document_loader import load_pdf_document
from business_analysis_agents.fixed_resources import (
    DEFAULT_WORKFLOW_ONTOLOGY,
    load_fixed_turtle,
)
from business_analysis_agents.models import (
    AgentName,
    OntologyValidationResult,
    RdfKind,
    SelfReviewResult,
    SelfReviewRunStatus,
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
from business_analysis_agents.self_review import run_self_review_loop


DEFAULT_MAX_WORKFLOW_ITERATIONS = 3
DEFAULT_MAX_WORKFLOW_SELF_REVIEW_ITERATIONS = 3
DEBUG_OUTPUT_FILENAMES = (
    "workflow_ontology_v0_1.ttl",
    "workflow_shapes_v0_1.ttl",
    "workflow_ontology_design.json",
    "workflow_ontology_validation.json",
    "workflow_ontology_history.json",
    "workflow_agent_output.json",
    "workflow_run_metadata.json",
)


def load_scenario_rdf(path: Path | str) -> str:
    """Load Scenario RDF Turtle from disk."""

    scenario_path = Path(path)
    if scenario_path.is_dir():
        scenario_path = scenario_path / "scenario_final.ttl"
    if not scenario_path.exists():
        raise FileNotFoundError(
            f"Scenario RDF was not found: {scenario_path}. "
            "Pass the path to scenario_final.ttl, for example "
            "--scenario outputs/scenario/scenario_final.ttl"
        )
    scenario_turtle = scenario_path.read_text(encoding="utf-8")
    if not scenario_turtle.strip():
        raise ValueError(f"Scenario RDF is empty: {scenario_path}")
    return scenario_turtle


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


def _require_self_review(output: WorkflowAgentOutput) -> SelfReviewResult:
    result = output.self_review_result
    if result is None:
        raise ValueError("Workflow Self-Review output did not include self_review_result.")
    if output.workflow_rdf_turtle or output.workflow_shacl_turtle:
        raise ValueError("Workflow Self-Review must not generate RDF or SHACL Turtle.")
    return result


def _assert_fixed_hashes(
    ontology_turtle: str,
    shacl_turtle: str,
    ontology_hash: str,
    shapes_hash: str,
) -> None:
    if content_hash(ontology_turtle) != ontology_hash:
        raise ValueError("Ontology changed after setup. Workflow revision must not change it.")
    if content_hash(shacl_turtle) != shapes_hash:
        raise ValueError(
            "SHACL shapes changed after generation. Workflow revision must not change them."
        )


def _remove_debug_outputs(workflow_dir: Path) -> None:
    """Remove stale debug artifacts from a completed normal run."""

    for filename in DEBUG_OUTPUT_FILENAMES:
        (workflow_dir / filename).unlink(missing_ok=True)


def run_workflow_pipeline(
    scenario_file: Path | str,
    model: str,
    pdf_file: Path | str,
    output_dir: Path | str = "outputs/workflow",
    max_workflow_iterations: int = DEFAULT_MAX_WORKFLOW_ITERATIONS,
    max_self_review_iterations: int = DEFAULT_MAX_WORKFLOW_SELF_REVIEW_ITERATIONS,
    runner: Callable[..., Any] | None = None,
    ontology_file: Path | str = DEFAULT_WORKFLOW_ONTOLOGY,
    save_debug_outputs: bool = False,
) -> dict[str, Any]:
    """Run Workflow RDF generation and save final artifacts."""

    scenario_path = Path(scenario_file)
    workflow_dir = Path(output_dir)
    workflow_dir.mkdir(parents=True, exist_ok=True)
    generated_shapes_path = workflow_dir / "workflow_shapes_generated.ttl"
    generated_shapes_path.unlink(missing_ok=True)
    scenario_turtle = load_scenario_rdf(scenario_path)
    pdf_path = Path(pdf_file)
    source_document = load_pdf_document(pdf_path)
    source_document_payload = source_document.model_dump(
        mode="json",
        exclude={"text"},
    )
    revision_history: list[dict[str, Any]] = []

    ontology_path, ontology_turtle = load_fixed_turtle(
        ontology_file, "Workflow ontology"
    )
    ontology_hash = content_hash(ontology_turtle)

    generation_output = run_workflow_agent(
        WorkflowAgentMode.WORKFLOW_GENERATION,
        {
            "scenario_rdf_turtle": scenario_turtle,
            "source_document": source_document_payload,
            "ontology_turtle": ontology_turtle,
            "ontology_hash": ontology_hash,
        },
        model=model,
        runner=runner,
    )
    workflow_turtle = _require_text(
        generation_output.workflow_rdf_turtle,
        "workflow_rdf_turtle",
    )
    shacl_output = run_workflow_agent(
        WorkflowAgentMode.WORKFLOW_SHACL_GENERATION,
        {
            "workflow_rdf_raw": workflow_turtle,
            "ontology_turtle": ontology_turtle,
            "ontology_hash": ontology_hash,
        },
        model=model,
        runner=runner,
    )
    shacl_turtle = _require_text(
        shacl_output.workflow_shacl_turtle,
        "workflow_shacl_turtle",
    )
    write_text(generated_shapes_path, shacl_turtle)

    ontology_validation = validate_ontology_and_shapes(ontology_turtle, shacl_turtle)
    if not ontology_validation.conforms:
        raise ValueError(
            "Generated Workflow SHACL validation failed: "
            f"{_ontology_validation_payload(ontology_validation)}"
        )
    shapes_hash = content_hash(shacl_turtle)

    if save_debug_outputs:
        write_text(workflow_dir / "workflow_ontology_v0_1.ttl", ontology_turtle)
        write_json(
            workflow_dir / "workflow_ontology_design.json",
            {
                "ontology_source": "fixed_ttl",
                "ontology_file": str(ontology_path),
                "shapes_source": "ai_generated",
                "shapes_file": str(generated_shapes_path),
            },
        )
        write_json(
            workflow_dir / "workflow_ontology_validation.json",
            _ontology_validation_payload(ontology_validation),
        )
        write_json(
            workflow_dir / "workflow_ontology_history.json",
            {
                "ontology_fixed": True,
                "shapes_fixed_after_generation": True,
                "ontology_hash": ontology_hash,
                "shapes_hash": shapes_hash,
            },
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
                "scenario_rdf_turtle": scenario_turtle,
                "source_document": source_document_payload,
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
                "phase": "shacl_revision",
                "output": _output_payload(revision_output),
                "validation": _validation_payload(validation),
            }
        )

    def assert_fixed_resources() -> None:
        _assert_fixed_hashes(
            ontology_turtle,
            shacl_turtle,
            ontology_hash,
            shapes_hash,
        )

    def review_rdf(current_rdf: str, review_iteration: int) -> SelfReviewResult:
        review_output = run_workflow_agent(
            WorkflowAgentMode.WORKFLOW_SELF_REVIEW,
            {
                "scenario_rdf_turtle": scenario_turtle,
                "source_document": source_document_payload,
                "ontology_turtle": ontology_turtle,
                "workflow_shacl_turtle": shacl_turtle,
                "current_workflow_rdf": current_rdf,
                "current_validation": _validation_payload(validation),
                "self_review_iteration": review_iteration,
                "ontology_hash": ontology_hash,
                "shapes_hash": shapes_hash,
            },
            model=model,
            runner=runner,
        )
        return _require_self_review(review_output)

    def revise_from_self_review(
        current_rdf: str,
        self_review_result: SelfReviewResult,
        current_validation: WorkflowRdfValidationResult,
        iteration: int,
    ) -> tuple[str, dict[str, Any], WorkflowAgentOutput]:
        revision_output = run_workflow_agent(
            WorkflowAgentMode.WORKFLOW_REVISION,
            {
                "scenario_rdf_turtle": scenario_turtle,
                "source_document": source_document_payload,
                "ontology_turtle": ontology_turtle,
                "shacl_turtle": shacl_turtle,
                "previous_workflow_rdf": current_rdf,
                "self_review_result": self_review_result.model_dump(mode="json"),
                "validation_feedback": validation_feedback(current_validation),
                "previous_validation": _validation_payload(current_validation),
                "revision_history": revision_history,
                "ontology_hash": ontology_hash,
                "shapes_hash": shapes_hash,
            },
            model=model,
            runner=runner,
        )
        revised_rdf = _require_text(
            revision_output.workflow_rdf_turtle,
            "workflow_rdf_turtle",
        )
        return revised_rdf, _output_payload(revision_output), revision_output

    def validate_revised_rdf(
        revised_rdf: str,
        iteration: int,
    ) -> WorkflowRdfValidationResult:
        return validate_workflow_rdf(
            revised_rdf,
            ontology_turtle,
            shacl_turtle,
            iteration=iteration,
        )

    (
        workflow_turtle,
        validation,
        self_review_history,
        self_review_revision_output,
    ) = run_self_review_loop(
        rdf_turtle=workflow_turtle,
        validation=validation,
        rdf_kind=RdfKind.WORKFLOW,
        reviewer_agent=AgentName.WORKFLOW,
        max_revision_iterations=max_self_review_iterations,
        revision_history=revision_history,
        review_rdf=review_rdf,
        revise_rdf=revise_from_self_review,
        validate_rdf=validate_revised_rdf,
        serialize_validation=_validation_payload,
        assert_fixed_resources=assert_fixed_resources,
    )
    if self_review_revision_output is not None:
        final_output = self_review_revision_output

    final_status = (
        "completed"
        if validation.conforms
        and self_review_history.status is SelfReviewRunStatus.PASSED
        else "needs_review"
    )
    if not save_debug_outputs:
        _remove_debug_outputs(workflow_dir)
    write_text(workflow_dir / "workflow_final.ttl", workflow_turtle)
    write_json(workflow_dir / "workflow_validation.json", _validation_payload(validation))
    write_json(workflow_dir / "workflow_revision_history.json", revision_history)
    write_json(
        workflow_dir / "workflow_self_review.json",
        self_review_history.model_dump(mode="json"),
    )

    metadata = {
        "scenario_file": str(scenario_path),
        "pdf_file": str(pdf_path),
        "execution_datetime": datetime.now().isoformat(),
        "model": model,
        "agent_name": "workflow_agent",
        "ontology_source_file": str(ontology_path),
        "shapes_source": "ai_generated",
        "generated_shapes_file": str(generated_shapes_path),
        "ontology_hash": ontology_hash,
        "shapes_hash": shapes_hash,
        "max_workflow_iterations": max_workflow_iterations,
        "max_self_review_iterations": max_self_review_iterations,
        "self_review_status": self_review_history.status.value,
        "final_status": final_status,
    }
    if save_debug_outputs:
        write_json(
            workflow_dir / "workflow_agent_output.json",
            _output_payload(final_output),
        )
        write_json(workflow_dir / "workflow_run_metadata.json", metadata)

    return {
        "output_dir": str(workflow_dir),
        "final_status": final_status,
        "ontology_validation": _ontology_validation_payload(ontology_validation),
        "workflow_validation": _validation_payload(validation),
        "workflow_self_review": self_review_history.model_dump(mode="json"),
        "metadata": metadata,
    }
