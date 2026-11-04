from __future__ import annotations

import json
import re

import httpx

from app import config


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


def generate_narrative(facts: str, fallback_summary: str) -> tuple[str, list[str]] | None:
    """可选的大模型文案润色；失败时返回 None，由模板兜底。"""
    if not llm_configured():
        return None
    system = (
        "你是中文旅行规划助手。只能根据给定事实写 60 字以内的行程概述和 3 条实用提示。"
        "不得虚构地点、价格、营业时间或天气。只输出 JSON："
        '{"summary": "...", "tips": ["...", "...", "..."]}'
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
                    "temperature": 0.2,
                },
            )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return None
    payload = _extract_json(str(content))
    if not payload:
        return None
    summary = str(payload.get("summary") or "").strip() or fallback_summary
    tips = [str(item).strip() for item in payload.get("tips") or [] if str(item).strip()][:3]
    return summary, tips
