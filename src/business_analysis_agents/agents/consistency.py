"""Consistency Agent for Cross-SHACL generation and violation analysis."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from agents import Agent, AgentOutputSchema, Runner
from agents.exceptions import ModelBehaviorError

from business_analysis_agents.models import (
    ConsistencyAgentMode,
    ConsistencyAgentOutput,
)


CONSISTENCY_AGENT_NAME = "consistency_agent"

BASE_CONSISTENCY_INSTRUCTIONS = """
あなたはWorkflow RDF、Data RDF、Rule RDF間の整合性を評価するConsistency Agentです。

全体ルール:
- 適合判定はPythonのpySHACLが行います。あなた自身が適合・不適合を決定しないでください。
- 入力された3つの固定Ontologyを変更しないでください。
- RDFやOntologyに存在しないClass、Property、対応関係を推測しないでください。
- 名前や説明が似ているだけの別URIを、同一リソースとして扱わないでください。
- RDF間に明示的な参照または対応付け語彙がない場合、その対応を必須にしないでください。
- TurtleはMarkdownコードフェンスで囲まないでください。
- 指定されたConsistencyAgentOutputの構造に従ってください。
""".strip()

CROSS_SHACL_GENERATION_INSTRUCTIONS = """
Mode: cross_shacl_generation
Workflow RDF、Data RDF、Rule RDFと、それぞれの固定Ontologyから、
RDF間整合性を検証するCross-SHACL Shapesを生成してください。

検証対象:
- あるRDFから別RDFへ明示的に参照されたURIが、統合Graph内に存在すること。
- 明示的な参照先が、固定Ontologyで要求された型と整合すること。
- 同一URIが複数RDFに現れる場合、明示された型や役割が矛盾しないこと。
- 固定OntologyにRDF間対応用Propertyがある場合、そのdomain、range、参照関係が整合すること。

制約:
- cross_shacl_turtleに完全なTurtle文字列を出力してください。
- SHACL標準語彙、RDF/RDFS/XSD、および入力された固定Ontologyの語彙だけを制約に使用してください。
- Cross-SHACL用Shape URIは新規作成できますが、業務用ClassやPropertyを新規作成しないでください。
- 個別SHACLと同じ単体必須項目検証を繰り返さず、RDF間の関係だけを対象にしてください。
- 特定の業務インスタンスをsh:targetNodeで列挙しないでください。
- 現在のRDFを単に通すために制約を弱めないでください。
- 明示的な相互参照がない関係を、名称類似などから推測して必須化しないでください。
- 入力RDFとOntologyは変更せず、cross_shacl_turtle以外のTurtleを出力しないでください。
""".strip()

VIOLATION_ANALYSIS_INSTRUCTIONS = """
Mode: violation_analysis
pySHACLが返したCross-SHACL違反を、入力RDFと固定Ontologyだけを根拠に解析してください。

ルール:
- violation_analysesに各違反の解析結果を出力してください。
- violation_indexは入力の違反番号と一致させてください。
- target_resourceには違反のfocus_nodeを基本として設定してください。
- causeには、どの明示的参照・型・Ontology制約が不整合かを記載してください。
- target_agentはworkflow、data、ruleのいずれかにしてください。
- repair_instructionは対象AgentがRDFだけを修正する具体的な指示にしてください。
- OntologyやCross-SHACLの変更を修正指示に含めないでください。
- 根拠のないリソースを追加するよう指示しないでください。
- 自動再実行は行わず、解析と指示の出力だけを行ってください。
- cross_shacl_turtleは出力しないでください。
""".strip()


class MissingOpenAIAPIKeyError(RuntimeError):
    """Raised when OPENAI_API_KEY is missing."""


def ensure_openai_api_key() -> None:
    """Ensure the OpenAI API key is available."""

    if not os.getenv("OPENAI_API_KEY"):
        raise MissingOpenAIAPIKeyError(
            "OPENAI_API_KEY is not set. Set it before running Consistency Agent."
        )


def build_consistency_agent(model: str) -> Agent:
    """Build the single Consistency Agent used by both internal modes."""

    return Agent(
        name=CONSISTENCY_AGENT_NAME,
        instructions=BASE_CONSISTENCY_INSTRUCTIONS,
        model=model,
        output_type=AgentOutputSchema(
            ConsistencyAgentOutput,
            strict_json_schema=False,
        ),
    )


def _mode_instructions(mode: ConsistencyAgentMode) -> str:
    if mode is ConsistencyAgentMode.CROSS_SHACL_GENERATION:
        return CROSS_SHACL_GENERATION_INSTRUCTIONS
    return VIOLATION_ANALYSIS_INSTRUCTIONS


def build_consistency_prompt(
    mode: ConsistencyAgentMode,
    payload: dict[str, Any],
) -> str:
    """Build a mode-specific Consistency Agent prompt."""

    return (
        f"{_mode_instructions(mode)}\n\n"
        f"mode='{mode.value}' のConsistencyAgentOutputを返してください。"
        "JSONキーを重複させないでください。不要なフィールドは省略してください。\n\n"
        f"入力ペイロード:\n{payload}"
    )


def _first_non_null_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key not in result or result[key] is None:
            result[key] = value
    return result


def _recover_output_from_model_error(
    error: ModelBehaviorError,
) -> ConsistencyAgentOutput | None:
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
    return ConsistencyAgentOutput.model_validate(payload)


def run_consistency_agent(
    mode: ConsistencyAgentMode,
    payload: dict[str, Any],
    model: str,
    runner: Callable[..., Any] | None = None,
) -> ConsistencyAgentOutput:
    """Run Cross-SHACL generation or violation analysis."""

    ensure_openai_api_key()
    agent = build_consistency_agent(model)
    prompt = build_consistency_prompt(mode, payload)
    run = runner or Runner.run_sync
    try:
        result = run(agent, prompt)
    except ModelBehaviorError as error:
        recovered = _recover_output_from_model_error(error)
        if recovered is not None:
            output = recovered
        else:
            raise
    else:
        final_output = getattr(result, "final_output", result)
        if isinstance(final_output, ConsistencyAgentOutput):
            output = final_output
        else:
            output = ConsistencyAgentOutput.model_validate(final_output)

    if output.mode is not mode:
        raise ValueError(
            "Consistency Agent returned an unexpected mode: "
            f"expected={mode.value}, actual={output.mode.value}"
        )
    return output
