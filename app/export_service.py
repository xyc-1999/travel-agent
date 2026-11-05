from __future__ import annotations

from dataclasses import dataclass

from app.export import build_doc_rtf, build_docx, build_markdown, build_pdf
from app.models import Itinerary


@dataclass(frozen=True)
class ExportPayload:
    content: bytes
    media_type: str


def render_export(itinerary: Itinerary, format: str) -> ExportPayload:
    if format == "docx":
        return ExportPayload(
            build_docx(itinerary),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    if format == "doc":
        return ExportPayload(build_doc_rtf(itinerary), "application/msword")
    if format == "pdf":
        return ExportPayload(build_pdf(itinerary), "application/pdf")
    return ExportPayload(build_markdown(itinerary).encode("utf-8"), "text/markdown; charset=utf-8")
