"""Cross Review pipeline for Workflow, Data, and Rule RDF."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from business_analysis_agents.agents.consistency import run_consistency_agent
from business_analysis_agents.fixed_resources import (
    DEFAULT_DATA_ONTOLOGY,
    DEFAULT_RULE_ONTOLOGY,
    DEFAULT_WORKFLOW_ONTOLOGY,
    load_fixed_turtle,
)
from business_analysis_agents.models import (
    AgentName,
    ConsistencyAgentMode,
    ConsistencyEvaluationInput,
    ConsistencyEvaluationResult,
    ConsistencyViolationAnalysis,
    CrossRdfValidationResult,
    ReviewStatus,
)
from business_analysis_agents.rdf_validation import (
    content_hash,
    validate_cross_rdf,
    validate_ontology_and_shapes,
)
from business_analysis_agents.progress import (
    ProgressReporter,
    ProgressStatus,
    progress_operation,
    report_progress,
)
from business_analysis_agents.workflow_pipeline import write_json, write_text


DEFAULT_WORKFLOW_RDF = Path("outputs/workflow/workflow_final.ttl")
DEFAULT_DATA_RDF = Path("outputs/data_rule/data_final.ttl")
DEFAULT_RULE_RDF = Path("outputs/data_rule/rule_final.ttl")
DEFAULT_WORKFLOW_VALIDATION = Path("outputs/workflow/workflow_validation.json")
DEFAULT_DATA_VALIDATION = Path("outputs/data_rule/data_validation.json")
DEFAULT_RULE_VALIDATION = Path("outputs/data_rule/rule_validation.json")


def _load_required_turtle(path: Path | str, label: str) -> tuple[Path, str]:
    resource_path = Path(path)
    if not resource_path.is_file():
        raise FileNotFoundError(f"Required {label} was not found: {resource_path}")
    turtle = resource_path.read_text(encoding="utf-8")
    if not turtle.strip():
        raise ValueError(f"Required {label} is empty: {resource_path}")
    return resource_path.resolve(), turtle


def _require_cross_shacl(value: str | None) -> str:
    if not value:
        raise ValueError(
            "Consistency Agent output did not include cross_shacl_turtle."
        )
    return value


def _require_conforming_validation(path: Path | str, label: str) -> Path:
    validation_path = Path(path)
    if not validation_path.is_file():
        raise FileNotFoundError(
            f"Required {label} validation result was not found: {validation_path}"
        )
    try:
        payload = json.loads(validation_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid {label} validation JSON: {validation_path}: {error}"
        ) from error
    if payload.get("conforms") is not True:
        raise ValueError(
            f"{label} RDF has not passed individual validation: {validation_path}"
        )
    return validation_path.resolve()


def _validation_payload(validation: CrossRdfValidationResult) -> dict[str, Any]:
    payload = validation.model_dump(mode="json")
    payload["conforms"] = validation.conforms
    return payload


def _validate_analyses(
    analyses: list[ConsistencyViolationAnalysis],
    violation_count: int,
) -> None:
    actual_indices = [analysis.violation_index for analysis in analyses]
    expected_indices = list(range(violation_count))
    if sorted(actual_indices) != expected_indices:
        raise ValueError(
            "Consistency Agent must return exactly one analysis per violation. "
            f"expected={expected_indices}, actual={actual_indices}"
        )
    allowed_agents = {AgentName.WORKFLOW, AgentName.DATA, AgentName.RULE}
    invalid_agents = sorted(
        {
            analysis.target_agent.value
            for analysis in analyses
            if analysis.target_agent not in allowed_agents
        }
    )
    if invalid_agents:
        raise ValueError(
            "Consistency Agent selected unsupported repair targets: "
            f"{invalid_agents}"
        )


def _assert_fixed_hashes(
    ontology_turtles: tuple[str, str, str],
    ontology_hashes: tuple[str, str, str],
    cross_shacl_turtle: str,
    cross_shapes_hash: str,
) -> None:
    if tuple(content_hash(turtle) for turtle in ontology_turtles) != ontology_hashes:
        raise ValueError("A fixed ontology changed during Cross Review.")
    if content_hash(cross_shacl_turtle) != cross_shapes_hash:
        raise ValueError("Cross-SHACL changed after generation.")


def run_consistency_pipeline(
    model: str,
    workflow_file: Path | str = DEFAULT_WORKFLOW_RDF,
    data_file: Path | str = DEFAULT_DATA_RDF,
    rule_file: Path | str = DEFAULT_RULE_RDF,
    workflow_validation_file: Path | str = DEFAULT_WORKFLOW_VALIDATION,
    data_validation_file: Path | str = DEFAULT_DATA_VALIDATION,
    rule_validation_file: Path | str = DEFAULT_RULE_VALIDATION,
    output_dir: Path | str = "outputs/consistency",
    workflow_ontology_file: Path | str = DEFAULT_WORKFLOW_ONTOLOGY,
    data_ontology_file: Path | str = DEFAULT_DATA_ONTOLOGY,
    rule_ontology_file: Path | str = DEFAULT_RULE_ONTOLOGY,
    runner: Callable[..., Any] | None = None,
    cross_shacl_file: Path | str | None = None,
    expected_cross_shapes_hash: str | None = None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Generate or reuse Cross-SHACL, validate merged RDF, and analyze violations."""

    consistency_dir = Path(output_dir)
    consistency_dir.mkdir(parents=True, exist_ok=True)
    shapes_path = consistency_dir / "consistency_shapes_generated.ttl"
    validation_path = consistency_dir / "consistency_validation.json"
    evaluation_path = consistency_dir / "consistency_evaluation.json"
    stale_paths = (validation_path, evaluation_path)
    if cross_shacl_file is None:
        stale_paths = (shapes_path, *stale_paths)
    for stale_path in stale_paths:
        stale_path.unlink(missing_ok=True)

    workflow_path, workflow_turtle = _load_required_turtle(
        workflow_file, "Workflow RDF"
    )
    data_path, data_turtle = _load_required_turtle(data_file, "Data RDF")
    rule_path, rule_turtle = _load_required_turtle(rule_file, "Rule RDF")
    workflow_validation_path = _require_conforming_validation(
        workflow_validation_file, "Workflow"
    )
    data_validation_path = _require_conforming_validation(data_validation_file, "Data")
    rule_validation_path = _require_conforming_validation(rule_validation_file, "Rule")
    workflow_ontology_path, workflow_ontology = load_fixed_turtle(
        workflow_ontology_file, "Workflow ontology"
    )
    data_ontology_path, data_ontology = load_fixed_turtle(
        data_ontology_file, "Data ontology"
    )
    rule_ontology_path, rule_ontology = load_fixed_turtle(
        rule_ontology_file, "Rule ontology"
    )

    ontology_turtles = (workflow_ontology, data_ontology, rule_ontology)
    ontology_hashes = tuple(content_hash(turtle) for turtle in ontology_turtles)
    agent_input = ConsistencyEvaluationInput(
        workflow_rdf_turtle=workflow_turtle,
        data_rdf_turtle=data_turtle,
        rule_rdf_turtle=rule_turtle,
        workflow_ontology_turtle=workflow_ontology,
        data_ontology_turtle=data_ontology,
        rule_ontology_turtle=rule_ontology,
    )
    if cross_shacl_file is None:
        with progress_operation(
            progress,
            phase="consistency",
            step="cross_shacl_generation",
            message="Cross-SHACL generation",
            completed_message="Cross-SHACL generated",
        ):
            shacl_output = run_consistency_agent(
                ConsistencyAgentMode.CROSS_SHACL_GENERATION,
                agent_input.model_dump(mode="json", exclude_none=True),
                model=model,
                runner=runner,
            )
        cross_shacl_turtle = _require_cross_shacl(shacl_output.cross_shacl_turtle)
        write_text(shapes_path, cross_shacl_turtle)
        cross_shacl_source = "ai_generated"
    else:
        report_progress(
            progress,
            phase="consistency",
            step="cross_shacl_generation",
            status=ProgressStatus.RUNNING,
            message="Reusing generated Cross-SHACL",
        )
        cross_shacl_source_path, cross_shacl_turtle = _load_required_turtle(
            cross_shacl_file,
            "generated Cross-SHACL",
        )
        if cross_shacl_source_path != shapes_path.resolve():
            write_text(shapes_path, cross_shacl_turtle)
        cross_shacl_source = "reused"

    combined_ontology = "\n\n".join(ontology_turtles)
    shapes_validation = validate_ontology_and_shapes(
        combined_ontology,
        cross_shacl_turtle,
    )
    if not shapes_validation.conforms:
        raise ValueError(
            "Generated Cross-SHACL validation failed: "
            f"{shapes_validation.model_dump(mode='json')}"
        )
    cross_shapes_hash = content_hash(cross_shacl_turtle)
    if (
        expected_cross_shapes_hash is not None
        and cross_shapes_hash != expected_cross_shapes_hash
    ):
        raise ValueError(
            "Cross-SHACL hash changed during consistency revision loop: "
            f"expected={expected_cross_shapes_hash}, actual={cross_shapes_hash}"
        )
    _assert_fixed_hashes(
        ontology_turtles,
        ontology_hashes,
        cross_shacl_turtle,
        cross_shapes_hash,
    )

    with progress_operation(
        progress,
        phase="consistency",
        step="cross_validation",
        message=(
            "Cross re-validation"
            if cross_shacl_file is not None
            else "Cross validation"
        ),
        completed_message="Cross validation completed",
    ):
        validation = validate_cross_rdf(
            workflow_turtle,
            data_turtle,
            rule_turtle,
            workflow_ontology,
            data_ontology,
            rule_ontology,
            cross_shacl_turtle,
        )
    write_json(validation_path, _validation_payload(validation))

    violations = (
        validation.shacl_result.violations if validation.shacl_result else []
    )
    report_progress(
        progress,
        phase="consistency",
        step="cross_validation",
        status=(
            ProgressStatus.PASSED
            if not violations and validation.conforms
            else ProgressStatus.FAILED
        ),
        message=(
            "Cross validation passed"
            if not violations and validation.conforms
            else f"Cross validation found {len(violations)} violation(s)"
        ),
    )
    analyses: list[ConsistencyViolationAnalysis] = []
    analysis_summary: str | None = None
    if violations:
        analysis_input = agent_input.model_copy(
            update={
                "cross_shacl_turtle": cross_shacl_turtle,
                "validation_result": validation.shacl_result,
            }
        )
        with progress_operation(
            progress,
            phase="consistency",
            step="violation_analysis",
            message="Cross violation analysis",
            completed_message="Cross violation analysis completed",
        ):
            analysis_output = run_consistency_agent(
                ConsistencyAgentMode.VIOLATION_ANALYSIS,
                analysis_input.model_dump(mode="json", exclude_none=True),
                model=model,
                runner=runner,
            )
        analyses = analysis_output.violation_analyses
        analysis_summary = analysis_output.summary
        _validate_analyses(analyses, len(violations))
        _assert_fixed_hashes(
            ontology_turtles,
            ontology_hashes,
            cross_shacl_turtle,
            cross_shapes_hash,
        )

    targets = {analysis.target_agent for analysis in analyses}
    target_agent = next(iter(targets)) if len(targets) == 1 else None
    conforms = validation.conforms
    if conforms:
        reason = "Cross-SHACL validation completed without violations."
    else:
        reason = analysis_summary or (
            f"Cross-SHACL validation found {len(violations)} violation(s)."
        )
    evaluation = ConsistencyEvaluationResult(
        status=ReviewStatus.APPROVED if conforms else ReviewStatus.NEEDS_REVISION,
        conforms=conforms,
        can_auto_repair=bool(analyses) and len(analyses) == len(violations),
        target_agent=target_agent,
        reason=reason,
        related_violation_indices=[analysis.violation_index for analysis in analyses],
        violations=violations,
        violation_analyses=analyses,
        cross_shapes_hash=cross_shapes_hash,
    )
    write_json(evaluation_path, evaluation.model_dump(mode="json"))

    return {
        "output_dir": str(consistency_dir),
        "final_status": "completed" if conforms else "needs_revision",
        "workflow_file": str(workflow_path),
        "data_file": str(data_path),
        "rule_file": str(rule_path),
        "workflow_validation_file": str(workflow_validation_path),
        "data_validation_file": str(data_validation_path),
        "rule_validation_file": str(rule_validation_path),
        "workflow_ontology_file": str(workflow_ontology_path),
        "data_ontology_file": str(data_ontology_path),
        "rule_ontology_file": str(rule_ontology_path),
        "shapes_validation": shapes_validation.model_dump(mode="json"),
        "cross_shacl_file": str(shapes_path.resolve()),
        "cross_shacl_source": cross_shacl_source,
        "cross_shapes_hash": cross_shapes_hash,
        "validation": _validation_payload(validation),
        "evaluation": evaluation.model_dump(mode="json"),
    }
