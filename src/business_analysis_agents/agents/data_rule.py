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
- Ontology TTLを生成・変更しないでください。
- SHACL生成モード以外ではSHACL TTLを生成・変更しないでください。
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
プロンプトで与えられた固定済みのontology_turtleを使ってください。
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

DATA_SHACL_GENERATION_INSTRUCTIONS = """
Mode: data_shacl_generation
生成済みのdata_rdf_rawと固定済みのontology_turtleを入力として、
Data RDFの構造的妥当性を検証するSHACL Shapesを生成してください。

ルール:
- data_shacl_turtleに、単独でRDFLibによりparse可能な完全なTurtle文字列を出力してください。
- TurtleをMarkdownコードフェンスで囲まないでください。
- 出力Turtleの先頭には、以下のprefix宣言を必ずそのまま含めてください。
  @prefix sh: <http://www.w3.org/ns/shacl#> .
  @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
  @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
  @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
- 上記prefixは使用しないものがあっても省略しないでください。
- 上記以外のprefixを使用する場合は、そのprefixもTurtle内で必ず宣言してください。
- 宣言されていないprefixは使用しないでください。
- 出力前に、Turtleとして構文的に完結していることを確認してください。

- SHACL標準語彙と、固定Ontology内でClassまたはPropertyとして明示的に定義されている語彙だけをSHACL制約に使用してください。
- sh:targetClass、sh:classには、固定Ontology内でrdfs:Classまたはowl:Classとして明示的に定義されたURIだけを使用してください。
- sh:pathには、固定Ontology内でPropertyとして明示的に定義されたURIだけを使用してください。
- rdfs:subClassOf、rdfs:domain、rdfs:range、schema:domainIncludes、schema:rangeIncludes等の参照先として登場するだけのURIを、ClassやPropertyとして直接使用しないでください。

- Propertyを特定ClassのShapeに設定する場合は、固定Ontologyのrdfs:domainまたはschema:domainIncludesを根拠にしてください。
- range制約を設定する場合も固定Ontologyのrdfs:rangeまたはschema:rangeIncludesを根拠にし、明示定義されていないClassをsh:classとして使用しないでください。
- 固定Ontologyに明示されていないdatatype変換や語彙間の意味的対応を一般知識から推測しないでください。
- 固定Ontologyから十分な根拠を得られない制約は追加しないでください。

- raw RDFに登場する対象クラスを参考にしつつ、単に現在のraw RDFだけを通すための制約にしないでください。
- 業務インスタンスURIをsh:targetNodeとして列挙するなど、特定のraw RDFへ過剰適合させないでください。
- 固定Ontologyに根拠のない必須値やカーディナリティを作らないでください。

- ontology_turtleとdata_rdf_rawは変更しないでください。
- data_rdf_turtleとrule_rdf_turtleは出力しないでください。
""".strip()

DATA_REVISION_INSTRUCTIONS = """
Mode: data_revision
data_rdf_turtleだけを修正してください。

PDF本文、Scenario RDF、現在のData RDF、RDFLibのparse error、語彙検証結果、
pySHACL結果、修正履歴を使って修正してください。ontology_turtleやData SHACL shapesは変更しないでください。
入力にself_review_resultがある場合は、各findingのevidenceとrevision_instructionも使ってください。
Self-Review findingでもPDF本文またはScenario RDFに根拠が確認できない変更は行わないでください。
入力にcross_consistency_revisionがある場合は、Data RDF向けに集約されたCross-SHACL違反、
原因、修正指示をまとめて処理してください。related_rdfsは参照整合性の確認にだけ使用してください。
入力にhuman_review_revisionがある場合は、人間の判断・補足回答と元のConsistency findingを
修正根拠として使用してください。PDF、Scenario RDF、人間の回答のいずれにも根拠のない情報は追加しないでください。
Cross違反の解消だけを目的としてPDF本文またはScenario RDFに根拠のないデータを追加しないでください。
SHACL違反を解消するためにPDF本文に根拠のない情報を追加しないでください。根拠不足の情報は削除するか、
必要に応じて未解決事項として残してください。
data_rdf_turtleは必須です。
""".strip()

DATA_SELF_REVIEW_INSTRUCTIONS = """
Mode: data_self_review
PDF本文、Scenario RDF、現在のData RDFを比較し、構文やSHACLではなく内容の意味的な
整合性と網羅性を自己評価してください。

確認事項:
- 文書、帳票、データ、データ項目、使用・生成されるデータが欠落していないか。
- データ種別やデータ間の関係を誤っていないか。
- PDFにもScenario RDFにも根拠のないデータや関係を追加していないか。
- 同一データを不必要に重複抽出していないか。
- PDFとScenario RDFの内容と矛盾していないか。
- human_review_revisionがある場合は、人間の回答も根拠として評価すること。

出力ルール:
- self_review_resultを必須で出力し、Data RDFやSHACLは出力しないでください。
- 問題がなければpassed=true、findings=[]としてください。
- 問題があればpassed=falseとし、category、target、description、evidence、
  revision_instructionをfindingごとに記録してください。
- reviewer_agent=data、rdf_kind=dataとしてください。
- evidenceにはsourceをPDFまたはScenario RDFとし、ページ番号やURI等をlocator、
  根拠となる記述をexcerptに入れてください。
- 根拠のない推測やOntology・SHACLの変更提案を含めないでください。
""".strip()

RULE_GENERATION_INSTRUCTIONS = """
Mode: rule_generation
プロンプトで与えられた固定済みのontology_turtle、PDF本文、Scenario RDF、
検証済みData RDFだけを使ってください。
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

RULE_SHACL_GENERATION_INSTRUCTIONS = """
Mode: rule_shacl_generation
生成済みのrule_rdf_rawと固定済みのontology_turtleを入力として、
Rule RDFの構造的妥当性を検証するSHACL Shapesを生成してください。

ルール:
- rule_shacl_turtleに、単独でRDFLibによりparse可能な完全なTurtle文字列を出力してください。
- TurtleをMarkdownコードフェンスで囲まないでください。
- 出力Turtleの先頭には、以下のprefix宣言を必ずそのまま含めてください。
  @prefix sh: <http://www.w3.org/ns/shacl#> .
  @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
  @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
  @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
- 上記prefixは使用しないものがあっても省略しないでください。
- 上記以外のprefixを使用する場合は、そのprefixもTurtle内で必ず宣言してください。
- 宣言されていないprefixは使用しないでください。
- 出力前に、Turtleとして構文的に完結していることを確認してください。

- SHACL標準語彙と、固定Ontology内でClassまたはPropertyとして明示的に定義されている語彙だけをSHACL制約に使用してください。
- sh:targetClass、sh:classには、固定Ontology内でrdfs:Classまたはowl:Classとして明示的に定義されたURIだけを使用してください。
- sh:pathには、固定Ontology内でPropertyとして明示的に定義されたURIだけを使用してください。
- rdfs:subClassOf、rdfs:domain、rdfs:range等の参照先として登場するだけのURIを、ClassやPropertyとして直接使用しないでください。

- Propertyを特定ClassのShapeに設定する場合は、固定Ontologyのrdfs:domainを根拠にしてください。
- range制約を設定する場合は固定Ontologyのrdfs:rangeを根拠にし、明示定義されていないClassをsh:classとして使用しないでください。
- 固定Ontologyに明示されていないdatatype変換や語彙間の意味的対応を一般知識から推測しないでください。
- 固定Ontologyから十分な根拠を得られない制約は追加しないでください。

- raw RDFに登場する対象クラスを参考にしつつ、単に現在のraw RDFだけを通すための制約にしないでください。
- 業務インスタンスURIをsh:targetNodeとして列挙するなど、特定のraw RDFへ過剰適合させないでください。
- 固定Ontologyに根拠のない必須値やカーディナリティを作らないでください。

- ontology_turtleとrule_rdf_rawは変更しないでください。
- data_rdf_turtleとrule_rdf_turtleは出力しないでください。
""".strip()

RULE_REVISION_INSTRUCTIONS = """
Mode: rule_revision
rule_rdf_turtleだけを修正してください。

PDF本文、Scenario RDF、前回のRule RDF、検証済みData RDF、固定済みontology_turtle、
固定済みRule SHACL shapes、RDFLibのparse error、語彙検証結果、pySHACL結果、修正履歴を使って
修正してください。ontology_turtleやRule SHACL shapesは変更しないでください。
入力にself_review_resultがある場合は、各findingのevidenceとrevision_instructionも使ってください。
Self-Review findingでもPDF本文またはScenario RDFに根拠が確認できない変更は行わないでください。
入力にcross_consistency_revisionがある場合は、Rule RDF向けに集約されたCross-SHACL違反、
原因、修正指示をまとめて処理してください。related_rdfsは参照整合性の確認にだけ使用してください。
入力にhuman_review_revisionがある場合は、人間の判断・補足回答と元のConsistency findingを
修正根拠として使用してください。PDF、Scenario RDF、人間の回答のいずれにも根拠のない情報は追加しないでください。
Cross違反の解消だけを目的としてPDF本文またはScenario RDFに根拠のないルールを追加しないでください。
SHACL違反を解消するためにPDF本文に根拠のない業務ルールを追加しないでください。
rule_rdf_turtleは必須です。
""".strip()

RULE_SELF_REVIEW_INSTRUCTIONS = """
Mode: rule_self_review
PDF本文、Scenario RDF、現在のRule RDFを比較し、構文やSHACLではなく内容の意味的な
整合性と網羅性を自己評価してください。入力された検証済みData RDFはデータ参照の確認だけに
使用してください。

確認事項:
- 判断条件、適用条件、数値条件、期間条件、例外条件が欠落していないか。
- 条件値、条件の意味、AND/OR等の関係を誤っていないか。
- PDFにもScenario RDFにも存在しないルールを追加していないか。
- Rule RDFからData RDFへの参照を誤っていないか。
- 同一ルールを不必要に重複抽出していないか。
- human_review_revisionがある場合は、人間の回答も根拠として評価すること。

出力ルール:
- self_review_resultを必須で出力し、Rule RDFやSHACLは出力しないでください。
- 問題がなければpassed=true、findings=[]としてください。
- 問題があればpassed=falseとし、category、target、description、evidence、
  revision_instructionをfindingごとに記録してください。
- reviewer_agent=rule、rdf_kind=ruleとしてください。
- evidenceにはsourceをPDFまたはScenario RDFとし、ページ番号やURI等をlocator、
  根拠となる記述をexcerptに入れてください。
- 根拠のない推測やOntology・SHACLの変更提案を含めないでください。
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
    if mode is DataRuleAgentMode.DATA_SHACL_GENERATION:
        return DATA_SHACL_GENERATION_INSTRUCTIONS
    if mode is DataRuleAgentMode.DATA_REVISION:
        return DATA_REVISION_INSTRUCTIONS
    if mode is DataRuleAgentMode.DATA_SELF_REVIEW:
        return DATA_SELF_REVIEW_INSTRUCTIONS
    if mode is DataRuleAgentMode.RULE_GENERATION:
        return RULE_GENERATION_INSTRUCTIONS
    if mode is DataRuleAgentMode.RULE_SHACL_GENERATION:
        return RULE_SHACL_GENERATION_INSTRUCTIONS
    if mode is DataRuleAgentMode.RULE_SELF_REVIEW:
        return RULE_SELF_REVIEW_INSTRUCTIONS
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
