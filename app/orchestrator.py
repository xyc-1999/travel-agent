from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Generator

from app.agents.budget import run_budget
from app.agents.research import run_research
from app.agents.route import run_route_planning
from app.agents.verification import run_verification
from app.models import (
    AgentTrace,
    Itinerary,
    RequirementInput,
    SourceRef,
    VerificationResult,
    WeatherDay,
)
from app.services.llm import generate_narrative
from app.services.weather import build_weather
from app.storage import get_session, save_trip


STAGE_META = [
    ("requirement", "需求理解"),
    ("research", "资料检索"),
    ("weather", "天气适配"),
    ("verification", "地点核验"),
    ("route", "路线编排"),
    ("budget", "预算计算"),
]


class PipelineError(RuntimeError):
    pass


def _trace(agent: str, label: str, summary: str, details: list[str], start: float, status: str = "done") -> AgentTrace:
    return AgentTrace(
        agent=agent,
        label=label,
        status=status,  # type: ignore[arg-type]
        summary=summary,
        details=details,
        duration_ms=int((time.perf_counter() - start) * 1000),
    )


def _source_label(verification: VerificationResult, weather_days: list[WeatherDay], llm_used: bool) -> tuple[bool, str]:
    parts: list[str] = []
    demo = False
    if verification.source_mode == "amap_live":
        parts.append("高德实时 POI（价格/营业时间未核验）")
    else:
        parts.append("内置示例地点库")
        demo = True
    if any(day.is_forecast for day in weather_days):
        parts.append("和风天气 3 天预报")
    else:
        parts.append("历史气候参考")
        demo = True
    parts.append("本地示例攻略")
    demo = True
    if llm_used:
        parts.append("大模型文案润色")
    return demo, " + ".join(parts)


def _default_summary(draft: RequirementInput, itinerary_days: int, total: float, per_person: float) -> str:
    interests = "、".join(draft.interests) if draft.interests else "核心景点与城市漫步"
    budget_text = f"预算估算 {total:.0f} 元（人均 {per_person:.0f} 元）"
    return (
        f"{draft.start_date.isoformat()} 至 {draft.end_date.isoformat()}，"
        f"{draft.destination} {itinerary_days} 天 {draft.travelers} 人，"
        f"围绕{interests}安排，节奏{draft.pace}。{budget_text}。"
    )


def _build_tips(draft: RequirementInput, weather_days: list[WeatherDay], budget_status: str) -> list[str]:
    tips = [
        "博物馆、热门景区通常需要提前预约，出发前请在官方渠道复核开放时间与闭馆日。",
        "行程中的票价、营业时间与住宿价格为示例或规则估算，不是实时报价。",
    ]
    bad_days = [day for day in weather_days if not day.outdoor_ok]
    if bad_days:
        dates = "、".join(day.date.strftime("%m-%d") for day in bad_days[:3])
        tips.append(f"{dates} 天气条件不适合长时间户外，已优先安排室内景点，建议携带雨具并保留机动时间。")
    if draft.origin and draft.origin != draft.destination:
        tips.append("往返大交通按距离规则估算，建议尽早购票并预留到站/值机时间。")
    if budget_status == "over_budget":
        tips.append("当前估算超出预算，可按建议调整住宿档次、付费景点或行程天数，系统不会自动改写你的偏好。")
    if draft.must_visit:
        tips.append("必去地点已优先纳入；若遇闭馆或核验失败，会在结果中明确标注，不会虚构可访问状态。")
    return tips[:5]


def iter_generation(session_id: str) -> Generator[dict, None, None]:
    session = get_session(session_id)
    if not session:
        yield {"event": "error", "data": {"message": "会话不存在或已过期，请重新填写需求。"}}
        return
    payload, analysis = session
    if analysis.status != "ready":
        yield {
            "event": "error",
            "data": {
                "message": "需求尚未澄清完成，不能生成行程。",
                "questions": [question.model_dump(mode="json") for question in analysis.questions],
            },
        }
        return

    draft = analysis.draft
    trace: list[AgentTrace] = []
    warnings: list[str] = list(analysis.assumptions)
    assumptions: list[str] = list(analysis.assumptions)

    # 1. 需求理解（复核）
    start = time.perf_counter()
    yield {"event": "agent_start", "data": {"agent": "requirement", "label": "需求理解", "index": 1, "total": len(STAGE_META)}}
    details = [analysis.summary]
    if analysis.assumptions:
        details.extend(analysis.assumptions[:3])
    requirement_trace = _trace("requirement", "需求理解", "已完成结构化需求与冲突检查", details, start)
    trace.append(requirement_trace)
    yield {"event": "agent_done", "data": requirement_trace.model_dump(mode="json")}

    # 2. 资料检索
    start = time.perf_counter()
    yield {"event": "agent_start", "data": {"agent": "research", "label": "资料检索", "index": 2, "total": len(STAGE_META)}}
    research = run_research(draft)
    research_details = [f"{snippet.title}（匹配分 {snippet.score}）" for snippet in research.snippets]
    if not research.snippets:
        research_details.append(research.note)
    research_trace = _trace(
        "research",
        "资料检索",
        f"检索到 {len(research.snippets)} 条示例攻略片段",
        research_details,
        start,
        "warning" if not research.snippets else "done",
    )
    trace.append(research_trace)
    yield {"event": "agent_done", "data": research_trace.model_dump(mode="json")}

    # 3. 天气
    start = time.perf_counter()
    yield {"event": "agent_start", "data": {"agent": "weather", "label": "天气适配", "index": 3, "total": len(STAGE_META)}}
    weather_days, weather_provider = build_weather(
        draft.destination or "",
        draft.start_date,  # type: ignore[arg-type]
        draft.end_date,  # type: ignore[arg-type]
        draft.demo_weather,
    )
    bad_days = [day for day in weather_days if not day.outdoor_ok]
    weather_details = [
        f"{day.date.strftime('%m-%d')}：{day.condition}，{day.low}~{day.high}℃，降水概率 {day.rain_probability}%（{day.source}）"
        for day in weather_days
    ]
    weather_trace = _trace(
        "weather",
        "天气适配",
        f"{len(weather_days)} 天天气约束，{len(bad_days)} 天不适合长时间户外",
        weather_details,
        start,
        "warning" if weather_provider != "qweather_forecast" else "done",
    )
    trace.append(weather_trace)
    yield {"event": "agent_done", "data": weather_trace.model_dump(mode="json")}

    # 4. 地点核验
    start = time.perf_counter()
    yield {"event": "agent_start", "data": {"agent": "verification", "label": "地点核验", "index": 4, "total": len(STAGE_META)}}
    verification = run_verification(draft)
    verified_attractions = [item.place.name for item in verification.places if item.place.category == "attraction"]
    verification_details = [
        f"可用地点 {len(verification.places)} 个（景点 {len(verified_attractions)}）",
        f"核验来源：{verification.source_mode}",
    ]
    if verification.must_visit_ids:
        verification_details.append(f"必去地点已核验 {len(verification.must_visit_ids)} 个")
    verification_details.extend(verification.warnings[:4])
    warnings.extend(verification.warnings)
    verification_status = "error" if not verified_attractions else ("warning" if verification.warnings else "done")
    verification_trace = _trace("verification", "地点核验", "完成地点匹配与开放日校验", verification_details, start, verification_status)
    trace.append(verification_trace)
    yield {"event": "agent_done", "data": verification_trace.model_dump(mode="json")}

    if not verified_attractions:
        yield {"event": "error", "data": {"message": "没有通过核验的景点，无法生成行程。", "details": verification.warnings}}
        return

    # 5. 路线编排
    start = time.perf_counter()
    yield {"event": "agent_start", "data": {"agent": "route", "label": "路线编排", "index": 5, "total": len(STAGE_META)}}
    days, route_warnings, adaptations = run_route_planning(draft, verification, weather_days)
    warnings.extend(route_warnings)
    attraction_count = sum(1 for day in days for item in day.items if item.kind == "attraction")
    route_details = [
        f"编排 {len(days)} 天，{attraction_count} 个景点",
        f"天气适配 {len(adaptations)} 处",
    ]
    route_details.extend(adaptations[:3])
    route_trace = _trace(
        "route",
        "路线编排",
        "完成按天分组、就近排序与时间轴编排",
        route_details,
        start,
        "warning" if route_warnings else "done",
    )
    trace.append(route_trace)
    yield {"event": "agent_done", "data": route_trace.model_dump(mode="json")}

    # 6. 预算
    start = time.perf_counter()
    yield {"event": "agent_start", "data": {"agent": "budget", "label": "预算计算", "index": 6, "total": len(STAGE_META)}}
    budget = run_budget(draft, days, verification)
    assumptions.extend(budget.assumptions)
    budget_details = [
        f"总估算 {budget.total:.0f} 元，人均 {budget.per_person:.0f} 元",
        f"预算状态：{budget.budget_status}",
    ]
    budget_details.extend(budget.suggestions[:3])
    budget_trace = _trace(
        "budget",
        "预算计算",
        "完成分项预算与预算口径校验",
        budget_details,
        start,
        "warning" if budget.budget_status == "over_budget" else "done",
    )
    trace.append(budget_trace)
    yield {"event": "agent_done", "data": budget_trace.model_dump(mode="json")}

    if budget.budget_status == "over_budget":
        warnings.append(
            f"估算总花费 {budget.total:.0f} 元，超出预算 {budget.over_budget_amount:.0f} 元；未自动更改已确认偏好。"
        )

    # 文案与来源
    facts = "\n".join(
        [
            f"目的地：{draft.destination}；日期：{draft.start_date} 至 {draft.end_date}；人数：{draft.travelers}；节奏：{draft.pace}",
            f"预算：总估算 {budget.total:.0f} 元，人均 {budget.per_person:.0f} 元，状态 {budget.budget_status}",
            *[
                f"第{day.day_index}天 {day.date} {day.theme}：" + "、".join(item.name for item in day.items if item.kind == "attraction")
                for day in days
            ],
        ]
    )
    fallback_summary = _default_summary(draft, len(days), budget.total, budget.per_person)
    narrative = generate_narrative(facts, fallback_summary)
    llm_used = narrative is not None
    summary, narrative_tips = narrative if narrative else (fallback_summary, [])
    tips = narrative_tips or _build_tips(draft, weather_days, budget.budget_status)

    demo_mode, data_mode_label = _source_label(verification, weather_days, llm_used)
    sources: list[SourceRef] = []
    if verification.source_mode == "amap_live":
        sources.append(
            SourceRef(
                type="place",
                title="高德地图 POI 检索",
                detail="名称、地址、坐标来自高德；营业时间与价格未核验。",
                level="live_poi",
                verified=True,
            )
        )
    else:
        sources.append(
            SourceRef(
                type="place",
                title="内置示例地点库",
                detail="用于演示地点一致性与开放日校验，不代表实时营业状态。",
                level="demo_catalog",
                verified=True,
            )
        )
    for snippet in research.snippets:
        sources.append(
            SourceRef(
                type="guide",
                title=snippet.title,
                detail=f"{snippet.source}：{snippet.text[:160]}...",
                level="sample_guide",
                verified=False,
            )
        )
    if any(day.is_forecast for day in weather_days):
        sources.append(
            SourceRef(
                type="weather",
                title="和风天气 3 天预报",
                detail="仅覆盖预报范围内的日期。",
                level="forecast",
                verified=True,
            )
        )
    else:
        sources.append(
            SourceRef(
                type="weather",
                title="历史气候参考",
                detail="按城市与月份整理的气候参考，不是天气预报。",
                level="climate_reference",
                verified=False,
            )
        )
    sources.append(
        SourceRef(
            type="budget",
            title="规则估算",
            detail="按示例票价、餐饮均价、住宿示例价与交通规则计算。",
            level="rule_estimate",
            verified=False,
        )
    )
    if llm_used:
        sources.append(
            SourceRef(type="llm", title="大模型文案", detail="仅润色概述与提示，未生成地点事实。", level="llm", verified=False)
        )

    itinerary = Itinerary(
        trip_id=uuid.uuid4().hex[:12],
        created_at=datetime.now(),
        destination=draft.destination or "",
        origin=draft.origin,
        start_date=draft.start_date,  # type: ignore[arg-type]
        end_date=draft.end_date,  # type: ignore[arg-type]
        days_count=len(days),
        travelers=draft.travelers,
        summary=summary,
        days=days,
        budget=budget,
        tips=tips,
        sources=sources,
        verification_summary=verification.warnings,
        demo_mode=demo_mode,
        data_mode_label=data_mode_label,
        assumptions=list(dict.fromkeys(assumptions)),
        warnings=list(dict.fromkeys(warnings)),
        agent_trace=trace,
        request_snapshot=draft,
    )
    save_trip(itinerary)

    # 导出 Agent 只在用户点击导出时执行；这里在 trace 中说明可用性。
    yield {
        "event": "result",
        "data": itinerary.model_dump(mode="json"),
    }


def generate_itinerary(session_id: str) -> Itinerary:
    result: Itinerary | None = None
    error_message: str | None = None
    for event in iter_generation(session_id):
        if event["event"] == "result":
            result = Itinerary.model_validate(event["data"])
        elif event["event"] == "error":
            error_message = event["data"].get("message", "生成失败")
    if result is None:
        raise PipelineError(error_message or "生成失败")
    return result
