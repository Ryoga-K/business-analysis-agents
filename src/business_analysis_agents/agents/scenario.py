"""業務シナリオ作成エージェント。"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents import Agent, AgentOutputSchema, Runner

from business_analysis_agents.models import ScenarioAgentInput, ScenarioAgentOutput


SCENARIO_AGENT_INSTRUCTIONS = """
あなたは業務分析を支援する研究用エージェントです。
入力された業務文書PDFの抽出テキストだけを根拠に、固定フォーマットの業務シナリオを作成してください。

制約:
- 業務文書に書かれていない内容を推測しない。
- 不明な内容、文書から断定できない内容は未確定事項へ入れる。
- 出力は指定されたPydanticモデルの構造に厳密に従う。
- 業務手順には、手順ID、手順名、説明、実行主体、入力データ、出力データ、前後関係、分岐条件、根拠、未確定かどうかを含める。
- 各業務手順の根拠には、根拠ページ番号と根拠テキストを必ず含める。
- 根拠テキストは入力中の原文から短く抜粋する。
- 根拠が見つからない手順は作成しない。不確かな候補は未確定事項に入れる。
""".strip()


class MissingOpenAIAPIKeyError(RuntimeError):
    """OPENAI_API_KEYが設定されていない場合のエラー。"""


def ensure_openai_api_key() -> None:
    """OpenAI APIキーが環境変数に設定されていることを確認する。"""

    if not os.getenv("OPENAI_API_KEY"):
        raise MissingOpenAIAPIKeyError(
            "OPENAI_API_KEY環境変数が設定されていません。.envまたは環境変数にAPIキーを設定してください。"
        )


def build_scenario_agent(model: str) -> Agent:
    """ScenarioAgentOutputを返すAgents SDKエージェントを定義する。"""

    return Agent(
        name="scenario_creation_agent",
        instructions=SCENARIO_AGENT_INSTRUCTIONS,
        model=model,
        output_type=AgentOutputSchema(
            ScenarioAgentOutput,
            strict_json_schema=False,
        ),
    )


def format_scenario_prompt(agent_input: ScenarioAgentInput) -> str:
    """ページ番号付きPDFテキストをシナリオ作成用プロンプトへ整形する。"""

    page_blocks = "\n\n".join(
        f"[page {page.page_number}]\n{page.text}" for page in agent_input.document.pages
    )
    document_text = page_blocks or agent_input.document.text
    return f"""
以下の業務文書から、固定フォーマットの業務シナリオを作成してください。

文書ID: {agent_input.document.document_id}
文書タイトル: {agent_input.document.title}

業務文書:
{document_text}
""".strip()


def run_scenario_agent(
    agent_input: ScenarioAgentInput,
    model: str,
    runner: Callable[..., Any] | None = None,
) -> ScenarioAgentOutput:
    """PDF抽出テキストから業務シナリオを生成する。"""

    ensure_openai_api_key()
    agent = build_scenario_agent(model)
    prompt = format_scenario_prompt(agent_input)
    run = runner or Runner.run_sync
    result = run(agent, prompt)
    final_output = getattr(result, "final_output", result)

    if isinstance(final_output, ScenarioAgentOutput):
        return final_output
    return ScenarioAgentOutput.model_validate(final_output)


def save_scenario_output(output: ScenarioAgentOutput, output_dir: Path | str) -> Path:
    """シナリオ生成結果をscenario.jsonとして保存する。"""

    destination = Path(output_dir) / "scenario.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination
