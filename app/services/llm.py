from __future__ import annotations

import json
import re

import httpx

from app import config

# 概述中一旦出现这些词，说明模型很可能补充了未提供的外部事实
UNSAFE_SUMMARY_TERMS = (
    "预约",
    "限流",
    "门票",
    "免费",
    "开放时间",
    "营业时间",
    "官网",
    "余票",
    "免票",
    "预订",
    "价格",
    "评分",
    "必去",
    "最佳",
    "推荐",
    "攻略",
    "闭馆",
    "限购",
    "排队",
    "等位",
)


def llm_configured() -> bool:
    return bool(config.LLM_API_KEY)


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _is_safe_summary(summary: str) -> bool:
    text = summary.strip()
    if not text or len(text) > 80:
        return False
    return not any(term in text for term in UNSAFE_SUMMARY_TERMS)


def generate_summary(facts: str, fallback_summary: str) -> str | None:
    """只允许模型润色概述，不生成提示或任何未提供的外部事实。"""
    if not llm_configured():
        return None
    system = (
        "你是中文旅行文案编辑。只能使用给定事实写 80 字以内的行程概述。"
        "禁止补充地点、价格、营业时间、预约规则、评价或任何未给出的信息；"
        "禁止使用“推荐、最佳、必去”等评价词。"
        '只输出 JSON：{"summary": "..."}'
    )
    try:
        with httpx.Client(timeout=config.LLM_TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{config.LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": facts},
                    ],
                    "temperature": 0.1,
                },
            )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return None
    payload = _extract_json(str(content))
    if not payload:
        return None
    summary = str(payload.get("summary") or "").strip()
    if not _is_safe_summary(summary):
        return None
    return summary or fallback_summary
