"""CLIから呼び出す最上位の制御処理。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from dotenv import load_dotenv

from business_analysis_agents.agents.scenario import (
    MissingOpenAIAPIKeyError,
    run_scenario_agent,
    save_scenario_output,
)
from business_analysis_agents.config import create_run_dir, load_config_from_env
from business_analysis_agents.document_loader import load_pdf_document
from business_analysis_agents.models import ScenarioAgentInput


def build_parser() -> argparse.ArgumentParser:
    """CLI引数パーサーを作成する。"""

    parser = argparse.ArgumentParser(
        description="業務文書PDFから固定フォーマットの業務シナリオを生成します。"
    )
    parser.add_argument(
        "pdf_path",
        nargs="?",
        help="業務文書PDFのパス。未指定の場合は起動確認メッセージだけを表示します。",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """プロトタイプを起動し、必要に応じてPDFからシナリオを生成する。"""

    args = build_parser().parse_args(list(argv) if argv is not None else [])
    if not args.pdf_path:
        print("システムを開始しました")
        return 0

    load_dotenv()
    config = load_config_from_env()
    try:
        document = load_pdf_document(args.pdf_path)
        output = run_scenario_agent(
            ScenarioAgentInput(document=document),
            model=config.openai_model,
        )
    except MissingOpenAIAPIKeyError as error:
        print(f"エラー: {error}")
        return 1

    run_dir = create_run_dir(config.output_dir)
    scenario_path = save_scenario_output(output, run_dir)
    print("システムを開始しました")
    print(f"シナリオを保存しました: {scenario_path}")
    return 0
