from __future__ import annotations

import io
import zipfile

from app.export import build_doc_rtf, build_docx, build_markdown, build_pdf
from app.models import Itinerary


def test_docx_is_valid_word_document(sample_itinerary: Itinerary):
    content = build_docx(sample_itinerary)
    assert content[:2] == b"PK"

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        assert "word/document.xml" in names
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert sample_itinerary.destination in document_xml
    assert "行程规划" in document_xml
    assert "每日行程" in document_xml


def test_pdf_is_valid_and_non_empty(sample_itinerary: Itinerary):
    content = build_pdf(sample_itinerary)

    assert content.startswith(b"%PDF")
    assert content.rstrip().endswith(b"%%EOF")
    assert len(content) > 5_000

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    assert len(reader.pages) >= 2


def test_doc_rtf_is_word_openable_ascii(sample_itinerary: Itinerary):
    content = build_doc_rtf(sample_itinerary)

    assert content.startswith(b"{\\rtf1")
    assert content.rstrip().endswith(b"}")

    text = content.decode("ascii")  # 必须全部是 ASCII 转义，避免中文乱码
    assert "\\u" in text
    assert "fonttbl" in text
    assert "Microsoft YaHei" in text


def test_markdown_contains_budget_and_days(sample_itinerary: Itinerary):
    text = build_markdown(sample_itinerary)

    assert sample_itinerary.destination in text
    assert "## 预算" in text
    assert "## 每日行程" in text
    assert "数据来源" in text
