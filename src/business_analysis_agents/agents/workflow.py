"""Workflow Agent with RDF generation and revision modes."""

from __future__ import annotations

import os
import json
from collections.abc import Callable
from typing import Any

from agents import Agent, AgentOutputSchema, Runner
from agents.exceptions import ModelBehaviorError

from business_analysis_agents.models import WorkflowAgentMode, WorkflowAgentOutput


WORKFLOW_AGENT_NAME = "workflow_agent"

BASE_WORKFLOW_INSTRUCTIONS = """
あなたはWorkflow RDF抽出を担当するAIエージェントです。

全体ルール:
- 業務シナリオJSONを業務全体の構造・流れの把握に使用してください。
- source_documentのページ番号付きPDF本文を、詳細情報と根拠情報の確認に使用してください。
- シナリオJSONにない情報でも、PDF本文に明確に記載され、Workflow RDFに必要であれば抽出してください。
- シナリオJSONとPDF本文が矛盾する場合は、原則としてPDF本文を根拠とし、矛盾をunresolved_itemsに記録してください。
- 与えられたOntology TTLを唯一の業務語彙体系として扱ってください。
- Ontology TTLやSHACL TTLを生成・変更しないでください。
- PDF本文にもシナリオJSONにも書かれていない活動、実行主体、データ、条件、順序、根拠を推測しないでください。
- 不確実な情報を無理に補完しないでください。不明点はunresolved_itemsに入れてください。
- すべてのモードでWorkflowAgentOutput Pydanticモデルを使って返してください。
- 説明用・設計用フィールドは、柔軟なJSON構造で返してかまいません。
- RDF構文、語彙、SHACL適合性はPydanticではなくRDFLibとpySHACLで検証されます。
- Workflow RDFは、完全なTurtle文字列としてworkflow_rdf_turtleに出力してください。
- TurtleをMarkdownコードフェンスで囲まないでください。
- 与えられたOntology TTLに定義されているClass・Propertyのみを使用してください。
- 業務固有のインスタンスURIは生成できますが、新しいClass・Propertyは作成しないでください。
- Ontology TTLのrdfs:label、rdfs:comment、rdfs:subClassOf、rdfs:domain、rdfs:rangeを参照し、最も適切な語彙を選択してください。
""".strip()

WORKFLOW_GENERATION_INSTRUCTIONS = """
Mode: workflow_generation
プロンプトで与えられた固定済みのontology_turtleとshacl_turtleを参照してください。
Workflow RDFを完全なTurtle文字列として直接生成してください。

シナリオJSONから業務全体の構造を把握し、PDF本文から業務活動、実行主体、
順序、分岐、入出力、例外、根拠の詳細を確認してください。

ルール:
- 与えられたOntology TTLに定義されているClass・Propertyのみを使用してください。
- 業務固有のインスタンスURIは生成できますが、新しいClass・Propertyは作成しないでください。
- Ontology TTLの定義を参照して、最も適切な語彙を選択してください。
- RDFの構文上必要なrdf:type以外は、Ontology TTLで定義されたPropertyだけを使ってください。
- 根拠を表すClass・PropertyがOntology TTLに定義されている場合だけ、根拠ページ番号と根拠テキストをRDFに含めてください。
- 根拠用語がOntology TTLに存在しない場合は、新しい語彙を作らずevidence_summaryに記録してください。
- 順序関係は、シナリオで順序が明示されている場合だけ作成してください。
- 同じ概念エンティティに複数のURIを割り当てないようにしてください。
- 不明な情報を補完せず、unresolved_itemsに入れてください。
- workflow_rdf_turtleは必須です。
""".strip()

WORKFLOW_REVISION_INSTRUCTIONS = """
Mode: workflow_revision
workflow_rdf_turtleだけを修正してください。

禁止事項:
- ontology_turtleを変更すること
- shacl_turtleを変更すること
- 新しい語彙を追加すること
- namespaceを変更すること
- 制約を弱めること
- 根拠のない業務情報を追加すること

PDF本文、シナリオJSON、RDFLibのparse error、語彙検証結果、SHACL検証結果、
現在のWorkflow RDF、修正履歴を使ってWorkflow RDFを修正してください。
SHACL違反を解消するためにPDF本文に根拠のない情報を追加しないでください。
根拠のない業務仮定を置かなければ修正できない問題は、
remaining_violationsとunresolved_itemsに残してください。
workflow_rdf_turtleは必須です。
""".strip()


class MissingOpenAIAPIKeyError(RuntimeError):
    """Raised when OPENAI_API_KEY is missing."""


def ensure_openai_api_key() -> None:
    """Ensure the OpenAI API key is available from the environment."""

    if not os.getenv("OPENAI_API_KEY"):
        raise MissingOpenAIAPIKeyError(
            "OPENAI_API_KEY is not set. Set it in the environment or .env before running Workflow Agent."
        )


def build_workflow_agent(model: str) -> Agent:
    """Build the single Agents SDK Workflow Agent."""

    return Agent(
        name=WORKFLOW_AGENT_NAME,
        instructions=BASE_WORKFLOW_INSTRUCTIONS,
        model=model,
        output_type=AgentOutputSchema(
            WorkflowAgentOutput,
            strict_json_schema=False,
        ),
    )


def _mode_instructions(mode: WorkflowAgentMode) -> str:
    if mode is WorkflowAgentMode.WORKFLOW_GENERATION:
        return WORKFLOW_GENERATION_INSTRUCTIONS
    return WORKFLOW_REVISION_INSTRUCTIONS


def build_workflow_prompt(mode: WorkflowAgentMode, payload: dict[str, Any]) -> str:
    """Build a mode-specific Workflow Agent prompt."""

    return (
        f"{_mode_instructions(mode)}\n\n"
        f"mode='{mode.value}' のWorkflowAgentOutputを返してください。"
        "このモードで必要なTurtle文字列だけが必須です。説明用フィールドは柔軟なJSONでかまいません。"
        "JSONキーを重複させないでください。不要なTurtleフィールドはnullにせず、省略してください。\n\n"
        f"入力ペイロード:\n{payload}"
    )


def _first_non_null_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a dict while preserving the first non-null value for duplicate keys."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key not in result or result[key] is None:
            result[key] = value
    return result


def _recover_output_from_model_error(error: ModelBehaviorError) -> WorkflowAgentOutput | None:
    """Recover structured output from an Agents SDK validation error when possible."""

    message = str(error)
    marker = "Invalid JSON when parsing "
    if marker not in message:
        return None
    start = message.find(marker) + len(marker)
    decoder = json.JSONDecoder(object_pairs_hook=_first_non_null_pairs)
    try:
        payload, _ = decoder.raw_decode(message[start:])
    except json.JSONDecodeError:
        return None
    return WorkflowAgentOutput.model_validate(payload)


def run_workflow_agent(
    mode: WorkflowAgentMode,
    payload: dict[str, Any],
    model: str,
    runner: Callable[..., Any] | None = None,
) -> WorkflowAgentOutput:
    """Run the single Workflow Agent in the requested internal mode."""

    ensure_openai_api_key()
    agent = build_workflow_agent(model)
    prompt = build_workflow_prompt(mode, payload)
    run = runner or Runner.run_sync
    try:
        result = run(agent, prompt)
    except ModelBehaviorError as error:
        recovered = _recover_output_from_model_error(error)
        if recovered is not None:
            return recovered
        raise
    final_output = getattr(result, "final_output", result)
    if isinstance(final_output, WorkflowAgentOutput):
        return final_output
    return WorkflowAgentOutput.model_validate(final_output)
