"""業務シナリオ作成エージェント。"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents import Agent, AgentOutputSchema, Runner

from business_analysis_agents.models import ScenarioAgentInput, ScenarioAgentOutput


SCENARIO_AGENT_INSTRUCTIONS = """
あなたは業務文書からScenario RDFを抽出する研究用エージェントです。

入力された業務文書の内容を業務情報の根拠とし、入力された固定Scenario Ontologyに従って、Scenario RDFを完全なTurtle文字列として生成してください。

制約:
- 業務文書に明示されていない業務情報を推測して補完しない。
- scenario_rdf_turtleに、単独でRDFLibによりparse可能な完全なTurtle文字列を出力する。
- TurtleをMarkdownコードフェンスで囲まない。
- 固定Scenario Ontologyに含まれるClass・Propertyのみを使用し、新しいClass・Propertyを作成しない。
- 業務固有のインスタンスURIは生成してよい。
- Class・Propertyの意味および関係構造は、固定Scenario Ontologyの定義に従う。
- Actor、UseCase、およびそれらの関係は、固定Scenario Ontologyで定義された構造に従って表現する。
- 名称や説明を表現する場合も、固定Scenario Ontologyに含まれる適切なPropertyを使用する。
- Scenario RDFでは、業務の全体像を把握するために必要な業務、Actor、UseCase、およびそれらの関係を中心に抽出する。
- 詳細な処理順序、分岐、数値条件、詳細な入出力、個別の業務ルールは含めすぎない。
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
    """固定Ontologyとページ番号付きPDF本文をプロンプトへ整形する。"""

    page_blocks = "\n\n".join(
        f"[page {page.page_number}]\n{page.text}" for page in agent_input.document.pages
    )
    document_text = page_blocks or agent_input.document.text
    return f"""
以下の固定Scenario Ontologyと業務文書からScenario RDFを生成してください。

固定Scenario Ontology TTL:
{agent_input.ontology_turtle}

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
    """PDF抽出テキストからScenario RDFを直接生成する。"""

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
    """Scenario RDFをscenario_final.ttlとして保存する。"""

    destination = Path(output_dir) / "scenario_final.ttl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(output.scenario_rdf_turtle, encoding="utf-8")
    return destination
