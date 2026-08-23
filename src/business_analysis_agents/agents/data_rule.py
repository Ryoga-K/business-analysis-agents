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
あなたは関連データ抽出とルール抽出を担当する唯一のAIエージェントです。
Data Agent、Rule Agent、Ontology Design Agentなどの別エージェントを作成しないでください。

全体ルール:
- 入力された業務シナリオJSONだけを業務情報の根拠にしてください。
- 元PDFを再読込したり、元PDFに基づく推測をしたりしないでください。
- シナリオに書かれていないデータ、属性、関係、判定条件、結果を推測しないでください。
- シナリオ内に既に含まれている根拠ページ番号と根拠テキストを使ってください。
- 不確実または根拠不足の内容はunresolved_itemsに残してください。
- 指示された場合、Data RDFとRule RDFは完全なTurtle文字列として直接出力してください。
- 中間的な業務RDF JSONは作成しないでください。
- RDF構文、語彙、SHACL適合性はPydanticではなくRDFLibとpySHACLで検証されます。
- 説明用・設計用フィールドは、柔軟なJSON構造で返してかまいません。
- revision系モードではontology_turtleやSHACL shapesを変更しないでください。
- TurtleをMarkdownコードフェンスで囲まないでください。
""".strip()

ONTOLOGY_SETUP_INSTRUCTIONS = """
Mode: ontology_setup
シナリオJSONを分析し、Data RDFとRule RDFに必要な暫定RDFS語彙を設計してください。
RDF、RDFS、XSD、PROV-Oは、実在し、意味が合う場合だけ再利用してください。
外部標準語彙のURIを勝手に作らないでください。

Data RDFの暫定概念として、例えば次を検討してください:
DataEntity, Document, Form, ApplicationForm, Notification, Result, DataAttribute,
Evidence, UnresolvedItem.

Data RDFの暫定プロパティとして、例えば次を検討してください:
hasAttribute, relatedTo, usedBy, generatedBy, submittedBy, issuedBy, managedBy,
hasEvidence, hasUnresolvedItem.

Rule RDFの暫定概念として、例えば次を検討してください:
BusinessRule, DecisionRule, EligibilityRule, Condition, Criterion, RuleOutcome,
Evidence, UnresolvedItem.

Rule RDFの暫定プロパティとして、例えば次を検討してください:
hasCondition, usesData, producesResult, appliesTo, evaluatedBy, hasOutcome,
hasEvidence, hasUnresolvedItem, dependsOnRule.

生成するフィールド:
- ontology_turtle (必須)
- data_shacl_turtle (必須)
- rule_shacl_turtle (必須)
- reused_standard_terms
- provisional_classes
- provisional_properties
- class_property_mapping
- ontology_design_notes
- data_shacl_design_notes
- rule_shacl_design_notes
- unresolved_design_issues
- ontology_version
- namespace_uri
""".strip()

DATA_GENERATION_INSTRUCTIONS = """
Mode: data_generation
プロンプトで与えられた固定済みのontology_turtleとData SHACL shapesだけを使ってください。
Data RDFを完全なTurtle文字列として直接生成してください。

シナリオで確認できるデータ概念だけを表現してください。対象には、文書、帳票、
通知、結果、明示されたデータ属性、業務活動によるデータ利用・生成、
明示された責任主体、根拠、未解決事項を含めてください。

シナリオに書かれていない汎用的な帳票属性を追加しないでください。
同じデータ概念に重複URIを割り当てないよう、正規化した識別子を使ってください。
data_rdf_turtleは必須です。
""".strip()

DATA_REVISION_INSTRUCTIONS = """
Mode: data_revision
data_rdf_turtleだけを修正してください。

RDFLibのparse error、語彙検証結果、pySHACL結果、修正履歴、
元のシナリオJSONを使って修正してください。ontology_turtleやData SHACL shapesは変更しないでください。
根拠のないシナリオ情報を追加しないでください。根拠不足の情報は削除するか、
必要に応じて未解決事項として残してください。
data_rdf_turtleは必須です。
""".strip()

RULE_GENERATION_INSTRUCTIONS = """
Mode: rule_generation
プロンプトで与えられた固定済みのontology_turtle、固定済みのRule SHACL shapes、
シナリオJSON、検証済みData RDFだけを使ってください。
Rule RDFを完全なTurtle文字列として直接生成してください。

シナリオで確認できる判定条件、資格・申請条件、除外条件、必要書類、
結果、データ参照だけを表現してください。シナリオで明示されていない数式、
閾値、AND/OR/NOT構造、形式的な式を作らないでください。
データを参照する場合は、可能な限り検証済みData RDFで定義されたエンティティを再利用してください。
rule_rdf_turtleは必須です。
""".strip()

RULE_REVISION_INSTRUCTIONS = """
Mode: rule_revision
rule_rdf_turtleだけを修正してください。

前回のRule RDF、検証済みData RDF、固定済みontology_turtle、固定済みRule SHACL shapes、
RDFLibのparse error、語彙検証結果、pySHACL結果、修正履歴、元のシナリオJSONを使って
修正してください。ontology_turtleやRule SHACL shapesは変更しないでください。
根拠のない業務ルールを追加しないでください。
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
    if mode is DataRuleAgentMode.ONTOLOGY_SETUP:
        return ONTOLOGY_SETUP_INSTRUCTIONS
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
