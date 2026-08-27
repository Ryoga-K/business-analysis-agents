"""PDFから業務シナリオを生成する最小機能のテスト。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from business_analysis_agents.agents import scenario as scenario_module
from business_analysis_agents.agents.scenario import (
    MissingOpenAIAPIKeyError,
    format_scenario_prompt,
    run_scenario_agent,
    save_scenario_output,
)
from business_analysis_agents.document_loader import load_pdf_document, load_pdf_pages
from business_analysis_agents.fixed_resources import DEFAULT_SCENARIO_ONTOLOGY
from business_analysis_agents.models import (
    ScenarioAgentInput,
    ScenarioAgentOutput,
    SourceDocument,
)


ONTOLOGY_TTL = """
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix dcmitype: <http://purl.org/dc/dcmitype/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
""".strip()

SCENARIO_TTL = """
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix dcmitype: <http://purl.org/dc/dcmitype/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix inst: <http://example.org/scenario/instance/> .

inst:order-subject
    dcterms:title "Order handling" ;
    dcterms:description "A clerk handles an order." ;
    dcterms:hasPart inst:order-package .

inst:clerk a prov:Agent ; dcterms:title "Clerk" .
inst:order-package a dcmitype:Collection ;
    dcterms:title "Order processing" ;
    dcterms:hasPart inst:check-order .
inst:check-order a prov:Activity ;
    dcterms:title "Check order" ;
    prov:wasAssociatedWith inst:clerk .
""".strip()


class FakePage:
    """PyMuPDFのページオブジェクトを模したテスト用クラス。"""

    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, mode: str) -> str:
        """指定モードを確認してページテキストを返す。"""

        assert mode == "text"
        return self.text


class FakeDocument:
    """PyMuPDFのDocumentを模したテスト用クラス。"""

    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    def __enter__(self) -> "FakeDocument":
        """コンテキストマネージャーとして自身を返す。"""

        return self

    def __exit__(self, *args: object) -> None:
        """終了時に追加処理をしない。"""

    def __iter__(self):
        """ページを順番に返す。"""

        return iter(self.pages)


def test_fixed_scenario_ontology_contains_required_vocabulary() -> None:
    """The fixed Scenario Ontology contains every term required by the prompt."""

    ontology = DEFAULT_SCENARIO_ONTOLOGY.read_text(encoding="utf-8")

    for term in (
        "dcterms:title",
        "dcterms:description",
        "dcterms:hasPart",
        "dcmitype:Collection",
        "prov:Agent",
        "prov:Activity",
        "prov:wasAssociatedWith",
    ):
        assert term in ontology


def test_load_pdf_pages_keeps_page_numbers(monkeypatch, tmp_path) -> None:
    """PDFテキストをページ番号付きで抽出できる。"""

    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")

    def fake_open(path):
        assert path == pdf_path
        return FakeDocument([FakePage("page one"), FakePage("page two")])

    monkeypatch.setattr("business_analysis_agents.document_loader.fitz.open", fake_open)

    pages = load_pdf_pages(pdf_path)
    document = load_pdf_document(pdf_path)

    assert [page.page_number for page in pages] == [1, 2]
    assert pages[0].text == "page one"
    assert document.pages[1].text == "page two"
    assert "[page 1]" in document.text


def test_run_scenario_agent_uses_structured_output(monkeypatch) -> None:
    """ScenarioAgentOutputを返すようにAgent実行をモックできる。"""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    document = SourceDocument(
        document_id="doc-1",
        title="manual",
        text="[page 1]\nA clerk checks an order.",
    )
    expected = ScenarioAgentOutput(scenario_rdf_turtle=SCENARIO_TTL)

    def fake_runner(agent, prompt):
        assert agent.output_type.output_type is ScenarioAgentOutput
        assert "推測しない" in agent.instructions
        assert "Markdownコードフェンス" in agent.instructions
        assert "Subjectにはrdf:typeを付与しない" in agent.instructions
        assert ONTOLOGY_TTL in prompt
        assert "[page 1]" in prompt
        return SimpleNamespace(final_output=expected)

    actual = run_scenario_agent(
        ScenarioAgentInput(document=document, ontology_turtle=ONTOLOGY_TTL),
        model="gpt-test",
        runner=fake_runner,
    )

    assert actual == expected


def test_run_scenario_agent_requires_api_key(monkeypatch) -> None:
    """APIキーがない場合は分かりやすい専用エラーになる。"""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    document = SourceDocument(document_id="doc-1", title="manual", text="text")

    with pytest.raises(MissingOpenAIAPIKeyError, match="OPENAI_API_KEY"):
        run_scenario_agent(
            ScenarioAgentInput(document=document, ontology_turtle=ONTOLOGY_TTL),
            model="gpt-test",
            runner=lambda *_args: None,
        )


def test_save_scenario_output_writes_turtle(tmp_path) -> None:
    """Scenario RDFをscenario_final.ttlとして保存できる。"""

    output = ScenarioAgentOutput(scenario_rdf_turtle=SCENARIO_TTL)

    path = save_scenario_output(output, tmp_path)

    assert path.name == "scenario_final.ttl"
    assert "Order handling" in path.read_text(encoding="utf-8")


def test_format_scenario_prompt_prefers_page_text() -> None:
    """ページ単位テキストがある場合はページ番号付きでプロンプト化する。"""

    document = SourceDocument(
        document_id="doc-1",
        title="manual",
        text="fallback",
        pages=[{"page_number": 2, "text": "page text"}],
    )

    prompt = format_scenario_prompt(
        ScenarioAgentInput(document=document, ontology_turtle=ONTOLOGY_TTL)
    )

    assert "[page 2]" in prompt
    assert "page text" in prompt
    assert "fallback" not in prompt
