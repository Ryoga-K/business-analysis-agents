"""Workflow Agent with ontology setup, RDF generation, and revision modes."""

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
あなたはWorkflow RDF抽出を担当する唯一のAIエージェントです。
Ontology Design Agentなどの別エージェントを作成したり、処理を委任したりしないでください。

全体ルール:
- ontology設計は、このWorkflow Agentの内部モードとして扱ってください。
- 入力された業務シナリオJSONだけを業務情報の根拠にしてください。
- シナリオに書かれていない活動、実行主体、データ、条件、順序、根拠を推測しないでください。
- 不確実な情報を無理に補完しないでください。不明点はunresolved_itemsに入れてください。
- すべてのモードでWorkflowAgentOutput Pydanticモデルを使って返してください。
- 説明用・設計用フィールドは、柔軟なJSON構造で返してかまいません。
- RDF構文、語彙、SHACL適合性はPydanticではなくRDFLibとpySHACLで検証されます。
- Workflow RDFは、完全なTurtle文字列としてworkflow_rdf_turtleに出力してください。
- TurtleをMarkdownコードフェンスで囲まないでください。
- workflow_revision中はontology_turtleやshacl_turtleを変更しないでください。
""".strip()

ONTOLOGY_SETUP_INSTRUCTIONS = """
Mode: ontology_setup
シナリオJSONを分析し、Workflow RDFに必要な暫定語彙を決めてください。
基本的なBPMN概念を参考にし、実用的な範囲でRDF、RDFS、XSD、PROV-Oを再利用してください。
複雑なOWL推論ではなく、RDFSレベルのontologyにしてください。

少なくとも次を表現できるようにしてください:
- 業務フロー全体
- 活動
- 実行主体
- 入力データ
- 出力データ
- 活動の順序
- 分岐条件
- 抽出根拠
- 未解決事項

クラス名にはWorkflow、Activity、Actor、DataObject、Condition、Evidenceなどを使ってかまいません。
プロパティ名にはhasActivity、performedBy、hasInput、hasOutput、
precedes、hasCondition、hasEvidence、sourcePage、evidenceTextなどを使ってかまいません。
これらの名前を必ず採用する必要はありません。シナリオに合う語彙を選び、
class_property_mappingに対応関係を記録してください。

生成するフィールド:
- ontology_turtle (必須)
- shacl_turtle (必須)
- reused_standard_terms
- provisional_classes
- provisional_properties
- class_property_mapping
- ontology_design_notes
- shacl_design_notes
- unresolved_design_issues
- ontology_version
- namespace_uri
""".strip()

WORKFLOW_GENERATION_INSTRUCTIONS = """
Mode: workflow_generation
プロンプトで与えられた固定済みのontology_turtleとshacl_turtleだけを使ってください。
Workflow RDFを完全なTurtle文字列として直接生成してください。

ルール:
- 固定ontologyで利用可能なクラス・プロパティ、および一般的なRDF/RDFS/XSD/PROV語彙だけを使ってください。
- 新しいクラス、プロパティ、namespaceを追加しないでください。
- プロンプトで与えられたnamespace URIの命名方針に従ってください。
- 主要な活動と条件には、根拠ページ番号と根拠テキストを含めてください。
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

RDFLibのparse error、語彙検証結果、SHACL検証結果、修正履歴を使って
Workflow RDFを修正してください。根拠のない業務仮定を置かなければ修正できない問題は、
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
    if mode is WorkflowAgentMode.ONTOLOGY_SETUP:
        return ONTOLOGY_SETUP_INSTRUCTIONS
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
