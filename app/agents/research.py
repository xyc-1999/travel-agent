from __future__ import annotations

import re

from app.data_loader import knowledge_chunks, normalize_city_name
from app.models import RequirementInput, ResearchResult, ResearchSnippet


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[\u4e00-\u9fa5]{2,}|[A-Za-z]{2,}", text):
        terms.add(token)
        if re.fullmatch(r"[\u4e00-\u9fa5]{2,}", token):
            for index in range(len(token) - 1):
                terms.add(token[index : index + 2])
    return terms


def run_research(draft: RequirementInput) -> ResearchResult:
    city = normalize_city_name(draft.destination)
    chunks = [chunk for chunk in knowledge_chunks() if chunk["city"] == city]
    if not chunks:
        return ResearchResult(
            snippets=[],
            source_mode="local_sample_guide",
            note=f"本地示例攻略未覆盖 {city or '该目的地'}，行程不会编造攻略引用。",
        )

    query_parts = [city]
    query_parts.extend(draft.interests)
    query_parts.extend(draft.must_visit)
    query_parts.append(draft.pace or "")
    query_parts.append(draft.notes or "")
    if draft.avoid:
        query_parts.append(" ".join(draft.avoid))
    query_terms = _terms(" ".join(query_parts))

    scored: list[tuple[float, dict]] = []
    for chunk in chunks:
        title_text = chunk["title"]
        body_text = chunk["text"]
        score = 0.0
        for term in query_terms:
            if term in title_text:
                score += 3.0
            if term in body_text:
                score += 1.0
        if city in body_text:
            score += 2.0
        scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [item for item in scored if item[0] > 0][:3] or scored[:2]

    snippets: list[ResearchSnippet] = []
    for score, chunk in selected:
        snippets.append(
            ResearchSnippet(
                source=chunk["source"],
                title=chunk["title"],
                text=chunk["text"][:420],
                score=round(score, 2),
            )
        )
    return ResearchResult(
        snippets=snippets,
        source_mode="local_sample_guide",
        note="来自 6 座城市的本地示例攻略片段，仅作规划参考，不是实时信息。",
    )
