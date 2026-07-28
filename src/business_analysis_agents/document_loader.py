"""PDF文書の読み込み処理。"""

from __future__ import annotations

from pathlib import Path

import fitz

from business_analysis_agents.models import PageText, SourceDocument


def load_pdf_pages(path: Path | str) -> list[PageText]:
    """PyMuPDFでPDFテキストをページ単位に抽出する。"""

    pdf_path = Path(path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDFファイルが見つかりません: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"PDFファイルを指定してください: {pdf_path}")

    pages: list[PageText] = []
    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document, start=1):
            pages.append(PageText(page_number=page_index, text=page.get_text("text")))
    return pages


def load_pdf_text(path: Path | str) -> str:
    """PDF全体のテキストをページ番号付きで結合して返す。"""

    pages = load_pdf_pages(path)
    return "\n\n".join(
        f"[page {page.page_number}]\n{page.text}".strip() for page in pages
    )


def load_pdf_document(path: Path | str) -> SourceDocument:
    """PDFをSourceDocumentモデルとして読み込む。"""

    pdf_path = Path(path)
    pages = load_pdf_pages(pdf_path)
    text = "\n\n".join(
        f"[page {page.page_number}]\n{page.text}".strip() for page in pages
    )
    return SourceDocument(
        document_id=pdf_path.stem,
        title=pdf_path.stem,
        text=text,
        source_path=str(pdf_path),
        pages=pages,
    )
