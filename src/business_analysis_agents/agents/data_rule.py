"""Related data and rule extraction agent."""

from __future__ import annotations

import os
import json
from collections.abc import Callable
from typing import Any

from agents import Agent, AgentOutputSchema, Runner
from agents.exceptions import ModelBehaviorError

from business_analysis_agents.models import DataRuleAgentMode, DataRuleAgentOutput


DATA_RULE_AGENT_NAME = "related_data_rule_agent"

BASE_DATA_RULE_INSTRUCTIONS = """
あなたは関連データ抽出とルール抽出を担当するAIエージェントです。

全体ルール:
- Scenario RDFを業務全体、Actor、主要UseCase、Package構造の把握に使用してください。
- source_documentのページ番号付きPDF本文を、詳細情報と根拠情報の確認に使用してください。
- Scenario RDFにない情報でも、PDF本文に明確に記載され、対象RDFに必要であれば抽出してください。
- Scenario RDFとPDF本文が矛盾する場合は、原則としてPDF本文を根拠とし、矛盾をunresolved_itemsに記録してください。
- 各処理で与えられたOntology TTLを唯一の業務語彙体系として扱ってください。
- Ontology TTLやSHACL TTLを生成・変更しないでください。
- PDF本文にもScenario RDFにも書かれていないデータ、属性、関係、判定条件、結果を推測しないでください。
- 根拠にはsource_documentのページ番号と本文を使用してください。
- 不確実または根拠不足の内容はunresolved_itemsに残してください。
- 指示された場合、Data RDFとRule RDFは完全なTurtle文字列として直接出力してください。
- 中間的な業務RDF JSONは作成しないでください。
- RDF構文、語彙、SHACL適合性はPydanticではなくRDFLibとpySHACLで検証されます。
- 説明用・設計用フィールドは、柔軟なJSON構造で返してかまいません。
- TurtleをMarkdownコードフェンスで囲まないでください。
- 与えられたOntology TTLに定義されているClass・Propertyのみを使用してください。
- 業務固有のインスタンスURIは生成できますが、新しいClass・Propertyは作成しないでください。
- Ontology TTLのrdfs:label、rdfs:comment、rdfs:subClassOf、rdfs:domain、rdfs:rangeを参照し、最も適切な語彙を選択してください。
""".strip()

DATA_GENERATION_INSTRUCTIONS = """
Mode: data_generation
プロンプトで与えられた固定済みのontology_turtleとData SHACL shapesだけを使ってください。
Data RDFを完全なTurtle文字列として直接生成してください。

与えられたOntology TTLに定義されているClass・Propertyのみを使用してください。
業務固有のインスタンスURIは生成できますが、新しいClass・Propertyは作成しないでください。
Ontology TTLの定義を参照して、最も適切な語彙を選択してください。
RDFの構文上必要なrdf:type以外は、Ontology TTLで定義されたPropertyだけを使ってください。

Scenario RDFまたはPDF本文で確認できるデータ概念だけを表現してください。対象には、文書、帳票、
通知、結果、データ項目、業務活動によるデータ利用・生成、データ間の関係、
明示された責任主体、根拠、未解決事項を含めてください。

PDF本文にもScenario RDFにも書かれていない汎用的な帳票属性を追加しないでください。
同じデータ概念に重複URIを割り当てないよう、正規化した識別子を使ってください。
data_rdf_turtleは必須です。
""".strip()

DATA_REVISION_INSTRUCTIONS = """
Mode: data_revision
data_rdf_turtleだけを修正してください。

PDF本文、Scenario RDF、現在のData RDF、RDFLibのparse error、語彙検証結果、
pySHACL結果、修正履歴を使って修正してください。ontology_turtleやData SHACL shapesは変更しないでください。
SHACL違反を解消するためにPDF本文に根拠のない情報を追加しないでください。根拠不足の情報は削除するか、
必要に応じて未解決事項として残してください。
data_rdf_turtleは必須です。
""".strip()

RULE_GENERATION_INSTRUCTIONS = """
Mode: rule_generation
プロンプトで与えられた固定済みのontology_turtle、固定済みのRule SHACL shapes、
PDF本文、Scenario RDF、検証済みData RDFだけを使ってください。
Rule RDFを完全なTurtle文字列として直接生成してください。

与えられたOntology TTLに定義されているClass・Propertyのみを使用してください。
業務固有のインスタンスURIは生成できますが、新しいClass・Propertyは作成しないでください。
Ontology TTLの定義を参照して、最も適切な語彙を選択してください。
RDFの構文上必要なrdf:type以外は、Ontology TTLで定義されたPropertyだけを使ってください。

Scenario RDFまたはPDF本文で確認できる判断条件、適用条件、資格・申請条件、
数値・期間条件、例外条件、分岐条件、必要書類、結果、データ参照だけを表現してください。
PDF本文にもScenario RDFにも明示されていない数式、
閾値、AND/OR/NOT構造、形式的な式を作らないでください。
データを参照する場合は、可能な限り検証済みData RDFで定義されたエンティティを再利用してください。
rule_rdf_turtleは必須です。
""".strip()

RULE_REVISION_INSTRUCTIONS = """
Mode: rule_revision
rule_rdf_turtleだけを修正してください。

PDF本文、Scenario RDF、前回のRule RDF、検証済みData RDF、固定済みontology_turtle、
固定済みRule SHACL shapes、RDFLibのparse error、語彙検証結果、pySHACL結果、修正履歴を使って
修正してください。ontology_turtleやRule SHACL shapesは変更しないでください。
SHACL違反を解消するためにPDF本文に根拠のない業務ルールを追加しないでください。
rule_rdf_turtleは必須です。
""".strip()


class MissingOpenAIAPIKeyError(RuntimeError):
    """Raised when OPENAI_API_KEY is missing."""


def ensure_openai_api_key() -> None:
    """Ensure the OpenAI API key is available from the environment."""

    if not os.getenv("OPENAI_API_KEY"):
        raise MissingOpenAIAPIKeyError(
            "OPENAI_API_KEY is not set. Set it in the environment or .env before running Data/Rule Agent."
        )


def build_data_rule_agent(model: str) -> Agent:
    """Build the single Agents SDK related data/rule extraction agent."""

    return Agent(
        name=DATA_RULE_AGENT_NAME,
        instructions=BASE_DATA_RULE_INSTRUCTIONS,
        model=model,
        output_type=AgentOutputSchema(
            DataRuleAgentOutput,
            strict_json_schema=False,
        ),
    )


def _mode_instructions(mode: DataRuleAgentMode) -> str:
    if mode is DataRuleAgentMode.DATA_GENERATION:
        return DATA_GENERATION_INSTRUCTIONS
    if mode is DataRuleAgentMode.DATA_REVISION:
        return DATA_REVISION_INSTRUCTIONS
    if mode is DataRuleAgentMode.RULE_GENERATION:
        return RULE_GENERATION_INSTRUCTIONS
    return RULE_REVISION_INSTRUCTIONS


def build_data_rule_prompt(mode: DataRuleAgentMode, payload: dict[str, Any]) -> str:
    """Build a mode-specific related data/rule extraction prompt."""

    return (
        f"{_mode_instructions(mode)}\n\n"
        f"mode='{mode.value}' のDataRuleAgentOutputを返してください。"
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


def _recover_output_from_model_error(error: ModelBehaviorError) -> DataRuleAgentOutput | None:
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
    return DataRuleAgentOutput.model_validate(payload)


def run_data_rule_agent(
    mode: DataRuleAgentMode,
    payload: dict[str, Any],
    model: str,
    runner: Callable[..., Any] | None = None,
) -> DataRuleAgentOutput:
    """Run the single related data/rule extraction agent in the requested mode."""

    ensure_openai_api_key()
    agent = build_data_rule_agent(model)
    prompt = build_data_rule_prompt(mode, payload)
    run = runner or Runner.run_sync
    try:
        result = run(agent, prompt)
    except ModelBehaviorError as error:
        recovered = _recover_output_from_model_error(error)
        if recovered is not None:
            return recovered
        raise
    final_output = getattr(result, "final_output", result)
    if isinstance(final_output, DataRuleAgentOutput):
        return final_output
    return DataRuleAgentOutput.model_validate(final_output)
