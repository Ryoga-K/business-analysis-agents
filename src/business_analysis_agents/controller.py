"""CLIから呼び出す最上位の制御処理。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from business_analysis_agents.agents.scenario import (
    MissingOpenAIAPIKeyError as ScenarioMissingOpenAIAPIKeyError,
    run_scenario_agent,
    save_scenario_output,
)
from business_analysis_agents.agents.data_rule import (
    MissingOpenAIAPIKeyError as DataRuleMissingOpenAIAPIKeyError,
)
from business_analysis_agents.agents.consistency import (
    MissingOpenAIAPIKeyError as ConsistencyMissingOpenAIAPIKeyError,
)
from business_analysis_agents.agents.workflow import (
    MissingOpenAIAPIKeyError as WorkflowMissingOpenAIAPIKeyError,
)
from business_analysis_agents.config import load_config_from_env
from business_analysis_agents.consistency_pipeline import (
    DEFAULT_DATA_RDF,
    DEFAULT_DATA_VALIDATION,
    DEFAULT_RULE_RDF,
    DEFAULT_RULE_VALIDATION,
    DEFAULT_WORKFLOW_RDF,
    DEFAULT_WORKFLOW_VALIDATION,
    run_consistency_pipeline,
)
from business_analysis_agents.data_rule_pipeline import (
    DEFAULT_MAX_DATA_ITERATIONS,
    DEFAULT_MAX_RULE_ITERATIONS,
    run_data_rule_pipeline,
)
from business_analysis_agents.fixed_resources import (
    DEFAULT_DATA_ONTOLOGY,
    DEFAULT_RULE_ONTOLOGY,
    DEFAULT_SCENARIO_ONTOLOGY,
    DEFAULT_WORKFLOW_ONTOLOGY,
    load_fixed_turtle,
)
from business_analysis_agents.document_loader import load_pdf_document
from business_analysis_agents.models import (
    ControllerRunSummary,
    ControllerStage,
    ControllerStageResult,
    ControllerStageStatus,
    RunStatus,
    ScenarioAgentInput,
)
from business_analysis_agents.review.cli import (
    DEFAULT_CONSISTENCY_EVALUATION as DEFAULT_REVIEW_CONSISTENCY_EVALUATION,
    DEFAULT_DATA_RDF as DEFAULT_REVIEW_DATA_RDF,
    DEFAULT_HUMAN_REVIEW_OUTPUT,
    DEFAULT_RULE_RDF as DEFAULT_REVIEW_RULE_RDF,
    DEFAULT_WORKFLOW_RDF as DEFAULT_REVIEW_WORKFLOW_RDF,
    HumanReviewInputError,
    run_human_review,
)
from business_analysis_agents.workflow_pipeline import (
    DEFAULT_MAX_WORKFLOW_ITERATIONS,
    run_workflow_pipeline,
    write_json,
)


def run_scenario_pipeline(
    pdf_file: Path | str,
    model: str,
    output_dir: Path | str = "outputs/scenario",
    ontology_file: Path | str = DEFAULT_SCENARIO_ONTOLOGY,
) -> dict[str, str]:
    """Run the existing Scenario generation flow and save Scenario RDF."""

    pdf_path = Path(pdf_file)
    document = load_pdf_document(pdf_path)
    ontology_path, scenario_ontology = load_fixed_turtle(
        ontology_file,
        "Scenario ontology",
    )
    output = run_scenario_agent(
        ScenarioAgentInput(
            document=document,
            ontology_turtle=scenario_ontology,
        ),
        model=model,
    )
    scenario_path = save_scenario_output(output, output_dir)
    return {
        "output_dir": str(Path(output_dir)),
        "final_status": "completed",
        "scenario_file": str(scenario_path),
        "ontology_file": str(ontology_path),
    }


def _stage_result(
    summary: ControllerRunSummary,
    stage: ControllerStage,
) -> ControllerStageResult:
    return next(result for result in summary.stages if result.stage is stage)


def _save_controller_summary(
    summary: ControllerRunSummary,
    summary_path: Path,
) -> None:
    write_json(summary_path, summary.model_dump(mode="json"))


def _finish_failed_run(
    summary: ControllerRunSummary,
    summary_path: Path,
    stage: ControllerStage,
    error_type: str,
    error_message: str,
    stage_status: ControllerStageStatus = ControllerStageStatus.FAILED,
) -> ControllerRunSummary:
    result = _stage_result(summary, stage)
    result.status = stage_status
    result.error_type = error_type
    result.error_message = error_message
    for later_result in summary.stages:
        if later_result.status is ControllerStageStatus.PENDING:
            later_result.status = ControllerStageStatus.SKIPPED
    summary.status = RunStatus.FAILED
    summary.completed = False
    summary.failed_stage = stage
    summary.error_message = error_message
    summary.finished_at = datetime.now().astimezone()
    _save_controller_summary(summary, summary_path)
    return summary


def run_end_to_end_controller(
    pdf_file: Path | str,
    model: str,
    output_dir: Path | str = "outputs",
    scenario_ontology_file: Path | str = DEFAULT_SCENARIO_ONTOLOGY,
    workflow_ontology_file: Path | str = DEFAULT_WORKFLOW_ONTOLOGY,
    data_ontology_file: Path | str = DEFAULT_DATA_ONTOLOGY,
    rule_ontology_file: Path | str = DEFAULT_RULE_ONTOLOGY,
    max_workflow_iterations: int = DEFAULT_MAX_WORKFLOW_ITERATIONS,
    max_data_iterations: int = DEFAULT_MAX_DATA_ITERATIONS,
    max_rule_iterations: int = DEFAULT_MAX_RULE_ITERATIONS,
    reviewer: str | None = None,
) -> ControllerRunSummary:
    """Run Scenario through Human Review in sequence."""

    root = Path(output_dir)
    scenario_dir = root / "scenario"
    workflow_dir = root / "workflow"
    data_rule_dir = root / "data_rule"
    consistency_dir = root / "consistency"
    summary_path = root / "controller" / "run_summary.json"
    stages = [
        ControllerStageResult(stage=stage)
        for stage in (
            ControllerStage.SCENARIO,
            ControllerStage.WORKFLOW,
            ControllerStage.DATA_RULE,
            ControllerStage.CONSISTENCY,
            ControllerStage.HUMAN_REVIEW,
        )
    ]
    summary = ControllerRunSummary(
        input_pdf=str(Path(pdf_file).resolve()),
        started_at=datetime.now().astimezone(),
        stages=stages,
        output_files={"run_summary": str(summary_path)},
    )
    _save_controller_summary(summary, summary_path)

    scenario_stage = _stage_result(summary, ControllerStage.SCENARIO)
    try:
        scenario_result = run_scenario_pipeline(
            pdf_file=pdf_file,
            model=model,
            output_dir=scenario_dir,
            ontology_file=scenario_ontology_file,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.SCENARIO,
            type(error).__name__,
            str(error),
        )
    scenario_file = scenario_result["scenario_file"]
    scenario_stage.status = ControllerStageStatus.COMPLETED
    scenario_stage.pipeline_status = scenario_result["final_status"]
    scenario_stage.output_files = {"scenario_rdf": scenario_file}
    summary.output_files.update(scenario_stage.output_files)
    _save_controller_summary(summary, summary_path)

    workflow_stage = _stage_result(summary, ControllerStage.WORKFLOW)
    try:
        workflow_result = run_workflow_pipeline(
            scenario_file=scenario_file,
            model=model,
            pdf_file=pdf_file,
            output_dir=workflow_dir,
            max_workflow_iterations=max_workflow_iterations,
            ontology_file=workflow_ontology_file,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.WORKFLOW,
            type(error).__name__,
            str(error),
        )
    workflow_stage.pipeline_status = workflow_result["final_status"]
    workflow_stage.output_files = {
        "workflow_rdf": str(workflow_dir / "workflow_final.ttl"),
        "workflow_shapes": str(workflow_dir / "workflow_shapes_generated.ttl"),
        "workflow_validation": str(workflow_dir / "workflow_validation.json"),
        "workflow_revision_history": str(
            workflow_dir / "workflow_revision_history.json"
        ),
    }
    summary.output_files.update(workflow_stage.output_files)
    if workflow_result["final_status"] != "completed":
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.WORKFLOW,
            "ValidationNotConforming",
            "Workflow RDF did not pass individual validation.",
            ControllerStageStatus.NEEDS_REVIEW,
        )
    workflow_stage.status = ControllerStageStatus.COMPLETED
    _save_controller_summary(summary, summary_path)

    data_rule_stage = _stage_result(summary, ControllerStage.DATA_RULE)
    try:
        data_rule_result = run_data_rule_pipeline(
            scenario_file=scenario_file,
            model=model,
            pdf_file=pdf_file,
            output_dir=data_rule_dir,
            max_data_iterations=max_data_iterations,
            max_rule_iterations=max_rule_iterations,
            data_ontology_file=data_ontology_file,
            rule_ontology_file=rule_ontology_file,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.DATA_RULE,
            type(error).__name__,
            str(error),
        )
    data_rule_stage.pipeline_status = data_rule_result["final_status"]
    data_rule_stage.output_files = {
        "data_rdf": str(data_rule_dir / "data_final.ttl"),
        "data_shapes": str(data_rule_dir / "data_shapes_generated.ttl"),
        "data_validation": str(data_rule_dir / "data_validation.json"),
        "data_revision_history": str(data_rule_dir / "data_revision_history.json"),
        "rule_rdf": str(data_rule_dir / "rule_final.ttl"),
        "rule_shapes": str(data_rule_dir / "rule_shapes_generated.ttl"),
        "rule_validation": str(data_rule_dir / "rule_validation.json"),
        "rule_revision_history": str(data_rule_dir / "rule_revision_history.json"),
    }
    summary.output_files.update(data_rule_stage.output_files)
    if data_rule_result["final_status"] != "completed":
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.DATA_RULE,
            "ValidationNotConforming",
            "Data RDF or Rule RDF did not pass individual validation.",
            ControllerStageStatus.NEEDS_REVIEW,
        )
    data_rule_stage.status = ControllerStageStatus.COMPLETED
    _save_controller_summary(summary, summary_path)

    consistency_stage = _stage_result(summary, ControllerStage.CONSISTENCY)
    try:
        consistency_result = run_consistency_pipeline(
            model=model,
            workflow_file=workflow_dir / "workflow_final.ttl",
            data_file=data_rule_dir / "data_final.ttl",
            rule_file=data_rule_dir / "rule_final.ttl",
            workflow_validation_file=workflow_dir / "workflow_validation.json",
            data_validation_file=data_rule_dir / "data_validation.json",
            rule_validation_file=data_rule_dir / "rule_validation.json",
            output_dir=consistency_dir,
            workflow_ontology_file=workflow_ontology_file,
            data_ontology_file=data_ontology_file,
            rule_ontology_file=rule_ontology_file,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.CONSISTENCY,
            type(error).__name__,
            str(error),
        )
    consistency_status = consistency_result["final_status"]
    consistency_stage.pipeline_status = consistency_status
    consistency_stage.output_files = {
        "consistency_shapes": str(
            consistency_dir / "consistency_shapes_generated.ttl"
        ),
        "consistency_validation": str(
            consistency_dir / "consistency_validation.json"
        ),
        "consistency_evaluation": str(
            consistency_dir / "consistency_evaluation.json"
        ),
    }
    summary.output_files.update(consistency_stage.output_files)
    if consistency_status not in {"completed", "needs_revision"}:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.CONSISTENCY,
            "UnexpectedPipelineStatus",
            f"Unexpected Consistency pipeline status: {consistency_status}",
        )
    consistency_stage.status = (
        ControllerStageStatus.COMPLETED
        if consistency_status == "completed"
        else ControllerStageStatus.NEEDS_REVIEW
    )
    _save_controller_summary(summary, summary_path)

    human_review_stage = _stage_result(summary, ControllerStage.HUMAN_REVIEW)
    human_review_file = root / "human_review" / "human_review.json"
    try:
        human_review = run_human_review(
            workflow_file=workflow_dir / "workflow_final.ttl",
            data_file=data_rule_dir / "data_final.ttl",
            rule_file=data_rule_dir / "rule_final.ttl",
            consistency_evaluation_file=(
                consistency_dir / "consistency_evaluation.json"
            ),
            output_file=human_review_file,
            reviewer=reviewer,
        )
    except Exception as error:
        return _finish_failed_run(
            summary,
            summary_path,
            ControllerStage.HUMAN_REVIEW,
            type(error).__name__,
            str(error),
        )
    human_review_stage.status = ControllerStageStatus.COMPLETED
    human_review_stage.pipeline_status = human_review.status.value
    human_review_stage.output_files = {
        "human_review": str(human_review_file),
    }
    summary.output_files.update(human_review_stage.output_files)
    summary.status = RunStatus.COMPLETED
    summary.completed = True
    summary.finished_at = datetime.now().astimezone()
    _save_controller_summary(summary, summary_path)
    return summary


def build_parser() -> argparse.ArgumentParser:
    """CLI引数パーサーを作成する。"""

    parser = argparse.ArgumentParser(
        description="業務文書PDFから固定Ontology準拠のScenario RDFを生成します。"
    )
    parser.add_argument(
        "pdf_path",
        nargs="?",
        help="業務文書PDFのパス。未指定の場合は起動確認メッセージだけを表示します。",
    )
    return parser


def build_workflow_parser() -> argparse.ArgumentParser:
    """Create the workflow subcommand parser."""

    workflow_parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents workflow",
        description="Generate Workflow RDF from Scenario RDF and a PDF.",
    )
    workflow_parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario_final.ttl generated by the Scenario Agent.",
    )
    workflow_parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the original business document PDF.",
    )
    workflow_parser.add_argument(
        "--output-dir",
        default="outputs/workflow",
        help="Directory for Workflow RDF artifacts.",
    )
    workflow_parser.add_argument(
        "--ontology",
        default=str(DEFAULT_WORKFLOW_ONTOLOGY),
        help="Workflow ontology Turtle file.",
    )
    workflow_parser.add_argument(
        "--max-workflow-iterations",
        type=int,
        default=3,
        help="Maximum Workflow RDF revision retries.",
    )
    workflow_parser.add_argument(
        "--save-debug-outputs",
        action="store_true",
        help="Save ontology, SHACL, agent output, and run metadata artifacts.",
    )
    return workflow_parser


def build_data_rule_parser() -> argparse.ArgumentParser:
    """Create the data-rule subcommand parser."""

    parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents data-rule",
        description="Generate Data RDF and Rule RDF from Scenario RDF and a PDF.",
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario_final.ttl generated by the Scenario Agent.",
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the original business document PDF.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/data_rule",
        help="Directory for Data RDF and Rule RDF artifacts.",
    )
    parser.add_argument(
        "--data-ontology",
        default=str(DEFAULT_DATA_ONTOLOGY),
        help="Data ontology Turtle file.",
    )
    parser.add_argument(
        "--rule-ontology",
        default=str(DEFAULT_RULE_ONTOLOGY),
        help="Rule ontology Turtle file.",
    )
    parser.add_argument(
        "--max-data-iterations",
        type=int,
        default=3,
        help="Maximum Data RDF revision retries.",
    )
    parser.add_argument(
        "--max-rule-iterations",
        type=int,
        default=3,
        help="Maximum Rule RDF revision retries.",
    )
    return parser


def build_consistency_parser() -> argparse.ArgumentParser:
    """Create the Cross Review subcommand parser."""

    parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents consistency",
        description="Cross-validate Workflow, Data, and Rule RDF.",
    )
    parser.add_argument("--workflow", default=str(DEFAULT_WORKFLOW_RDF))
    parser.add_argument("--data", default=str(DEFAULT_DATA_RDF))
    parser.add_argument("--rule", default=str(DEFAULT_RULE_RDF))
    parser.add_argument(
        "--workflow-validation",
        default=str(DEFAULT_WORKFLOW_VALIDATION),
    )
    parser.add_argument("--data-validation", default=str(DEFAULT_DATA_VALIDATION))
    parser.add_argument("--rule-validation", default=str(DEFAULT_RULE_VALIDATION))
    parser.add_argument("--output-dir", default="outputs/consistency")
    parser.add_argument(
        "--workflow-ontology",
        default=str(DEFAULT_WORKFLOW_ONTOLOGY),
    )
    parser.add_argument("--data-ontology", default=str(DEFAULT_DATA_ONTOLOGY))
    parser.add_argument("--rule-ontology", default=str(DEFAULT_RULE_ONTOLOGY))
    return parser


def build_human_review_parser() -> argparse.ArgumentParser:
    """Create the standalone Human Review subcommand parser."""

    parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents human-review",
        description="Interactively review Consistency findings.",
    )
    parser.add_argument("--workflow", default=str(DEFAULT_REVIEW_WORKFLOW_RDF))
    parser.add_argument("--data", default=str(DEFAULT_REVIEW_DATA_RDF))
    parser.add_argument("--rule", default=str(DEFAULT_REVIEW_RULE_RDF))
    parser.add_argument(
        "--consistency-evaluation",
        default=str(DEFAULT_REVIEW_CONSISTENCY_EVALUATION),
    )
    parser.add_argument("--output", default=str(DEFAULT_HUMAN_REVIEW_OUTPUT))
    parser.add_argument("--reviewer", default=None)
    return parser


def build_run_parser() -> argparse.ArgumentParser:
    """Create the End-to-End Controller subcommand parser."""

    parser = argparse.ArgumentParser(
        prog="python -m business_analysis_agents run",
        description="Run Scenario through interactive Human Review from one PDF.",
    )
    parser.add_argument("--pdf", required=True, help="Input business document PDF.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Root directory for all artifacts. Defaults to OUTPUT_DIR or outputs.",
    )
    parser.add_argument(
        "--scenario-ontology",
        default=str(DEFAULT_SCENARIO_ONTOLOGY),
    )
    parser.add_argument(
        "--workflow-ontology",
        default=str(DEFAULT_WORKFLOW_ONTOLOGY),
    )
    parser.add_argument("--data-ontology", default=str(DEFAULT_DATA_ONTOLOGY))
    parser.add_argument("--rule-ontology", default=str(DEFAULT_RULE_ONTOLOGY))
    parser.add_argument("--max-workflow-iterations", type=int, default=None)
    parser.add_argument("--max-data-iterations", type=int, default=None)
    parser.add_argument("--max-rule-iterations", type=int, default=None)
    parser.add_argument("--reviewer", default=None)
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """個別pipelineまたはEnd-to-End ControllerをCLIから実行する。"""

    raw_args = list(argv) if argv is not None else []
    load_dotenv()
    config = load_config_from_env()

    if raw_args[:1] == ["human-review"]:
        args = build_human_review_parser().parse_args(raw_args[1:])
        try:
            report = run_human_review(
                workflow_file=args.workflow,
                data_file=args.data,
                rule_file=args.rule,
                consistency_evaluation_file=args.consistency_evaluation,
                output_file=args.output,
                reviewer=args.reviewer,
            )
        except (
            FileNotFoundError,
            HumanReviewInputError,
            PermissionError,
            ValueError,
        ) as error:
            print(f"エラー: {error}")
            return 1
        print("Human Reviewを完了しました")
        print(f"出力: {args.output}")
        print(f"レビュー状態: {report.status.value}")
        return 0

    if raw_args[:1] == ["run"]:
        args = build_run_parser().parse_args(raw_args[1:])
        try:
            summary = run_end_to_end_controller(
                pdf_file=args.pdf,
                model=config.openai_model,
                output_dir=args.output_dir or config.output_dir,
                scenario_ontology_file=args.scenario_ontology,
                workflow_ontology_file=args.workflow_ontology,
                data_ontology_file=args.data_ontology,
                rule_ontology_file=args.rule_ontology,
                max_workflow_iterations=(
                    args.max_workflow_iterations
                    if args.max_workflow_iterations is not None
                    else config.max_repair_iterations
                ),
                max_data_iterations=(
                    args.max_data_iterations
                    if args.max_data_iterations is not None
                    else config.max_repair_iterations
                ),
                max_rule_iterations=(
                    args.max_rule_iterations
                    if args.max_rule_iterations is not None
                    else config.max_repair_iterations
                ),
                reviewer=args.reviewer,
            )
        except (FileNotFoundError, PermissionError, ValueError) as error:
            print(f"エラー: {error}")
            return 1
        print("End-to-End実行を終了しました")
        print(f"実行サマリー: {summary.output_files['run_summary']}")
        if summary.status is RunStatus.COMPLETED:
            consistency_status = _stage_result(
                summary,
                ControllerStage.CONSISTENCY,
            ).pipeline_status
            print(f"Consistency評価: {consistency_status}")
            human_review_status = _stage_result(
                summary,
                ControllerStage.HUMAN_REVIEW,
            ).pipeline_status
            print(f"Human Review: {human_review_status}")
            return 0
        print(f"失敗工程: {summary.failed_stage.value if summary.failed_stage else 'unknown'}")
        print(f"エラー: {summary.error_message}")
        return 1

    if raw_args[:1] == ["workflow"]:
        args = build_workflow_parser().parse_args(raw_args[1:])
        try:
            result = run_workflow_pipeline(
                scenario_file=args.scenario,
                model=config.openai_model,
                pdf_file=args.pdf,
                output_dir=args.output_dir,
                max_workflow_iterations=args.max_workflow_iterations,
                ontology_file=args.ontology,
                save_debug_outputs=args.save_debug_outputs,
            )
        except WorkflowMissingOpenAIAPIKeyError as error:
            print(f"エラー: {error}")
            return 1
        except (FileNotFoundError, PermissionError, ValueError) as error:
            print(f"エラー: {error}")
            return 1
        print("Workflow RDF生成を完了しました")
        print(f"出力先: {result['output_dir']}")
        print(f"最終状態: {result['final_status']}")
        return 0

    if raw_args[:1] == ["data-rule"]:
        args = build_data_rule_parser().parse_args(raw_args[1:])
        try:
            result = run_data_rule_pipeline(
                scenario_file=args.scenario,
                model=config.openai_model,
                pdf_file=args.pdf,
                output_dir=args.output_dir,
                max_data_iterations=args.max_data_iterations,
                max_rule_iterations=args.max_rule_iterations,
                data_ontology_file=args.data_ontology,
                rule_ontology_file=args.rule_ontology,
            )
        except DataRuleMissingOpenAIAPIKeyError as error:
            print(f"エラー: {error}")
            return 1
        except (FileNotFoundError, PermissionError, ValueError) as error:
            print(f"エラー: {error}")
            return 1
        print("Data RDF / Rule RDF生成を完了しました")
        print(f"出力先: {result['output_dir']}")
        print(f"最終状態: {result['final_status']}")
        return 0

    if raw_args[:1] == ["consistency"]:
        args = build_consistency_parser().parse_args(raw_args[1:])
        try:
            result = run_consistency_pipeline(
                model=config.openai_model,
                workflow_file=args.workflow,
                data_file=args.data,
                rule_file=args.rule,
                workflow_validation_file=args.workflow_validation,
                data_validation_file=args.data_validation,
                rule_validation_file=args.rule_validation,
                output_dir=args.output_dir,
                workflow_ontology_file=args.workflow_ontology,
                data_ontology_file=args.data_ontology,
                rule_ontology_file=args.rule_ontology,
            )
        except ConsistencyMissingOpenAIAPIKeyError as error:
            print(f"エラー: {error}")
            return 1
        except (FileNotFoundError, PermissionError, ValueError) as error:
            print(f"エラー: {error}")
            return 1
        print("Cross Reviewを完了しました")
        print(f"出力先: {result['output_dir']}")
        print(f"最終状態: {result['final_status']}")
        return 0

    args = build_parser().parse_args(raw_args)
    if not args.pdf_path:
        print("システムを開始しました")
        return 0

    try:
        scenario_result = run_scenario_pipeline(
            pdf_file=args.pdf_path,
            model=config.openai_model,
            output_dir=Path(config.output_dir) / "scenario",
        )
    except ScenarioMissingOpenAIAPIKeyError as error:
        print(f"エラー: {error}")
        return 1
    except (FileNotFoundError, PermissionError, ValueError) as error:
        print(f"エラー: {error}")
        return 1

    print("システムを開始しました")
    print(f"シナリオを保存しました: {scenario_result['scenario_file']}")
    return 0
