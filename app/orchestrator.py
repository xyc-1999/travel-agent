from __future__ import annotations

import uuid
from datetime import datetime
from threading import Event
from typing import Generator, cast

from app.models import (
    AgentTrace,
    BudgetPlan,
    Itinerary,
    RequirementInput,
    ResearchResult,
    SourceRef,
    VerificationResult,
    WeatherDay,
)
from app.pipeline import (
    STAGE_META,
    RouteOutcome,
    WeatherOutcome,
    budget_stage,
    requirement_stage,
    research_stage,
    route_stage,
    run_stage,
    verification_stage,
    weather_stage,
)
from app.services.llm import generate_summary
from app.storage import get_session, save_trip


class PipelineError(RuntimeError):
    pass


def _source_label(
    verification: VerificationResult,
    weather_days: list[WeatherDay],
    llm_used: bool,
    has_guides: bool,
) -> tuple[bool, str]:
    parts: list[str] = []
    demo = False
    if verification.source_mode == "amap_live":
        parts.append("高德实时 POI（参考价/营业时间按高德字段，缺失标注未获取）")
    else:
        parts.append("内置示例地点库")
        demo = True
    has_forecast = any(day.is_forecast for day in weather_days)
    has_reference = any(not day.is_forecast for day in weather_days)
    if has_forecast and has_reference:
        parts.append("和风天气预报 + 历史气候参考（按日期混合）")
        demo = True
    elif has_forecast:
        parts.append("和风天气预报")
    else:
        parts.append("历史气候参考")
        demo = True
    if has_guides:
        parts.append("本地示例攻略")
        demo = True
    if llm_used:
        parts.append("大模型文案润色")
    return demo, " + ".join(parts)


def _default_summary(
    draft: RequirementInput,
    itinerary_days: int,
    total: float,
    per_person: float,
    completeness: str = "complete",
) -> str:
    interests = "、".join(draft.interests) if draft.interests else "核心景点与城市漫步"
    label = "已知费用合计" if completeness == "partial" else "预算估算"
    budget_text = f"{label} {total:.0f} 元（人均 {per_person:.0f} 元）"
    if completeness == "partial":
        budget_text += "，不含未获取价格，0 元不代表免费"
    return (
        f"{draft.start_date.isoformat()} 至 {draft.end_date.isoformat()}，"
        f"{draft.destination} {itinerary_days} 天 {draft.travelers} 人，"
        f"围绕{interests}安排，节奏{draft.pace}。{budget_text}。"
    )


def _build_tips(
    draft: RequirementInput,
    weather_days: list[WeatherDay],
    budget: BudgetPlan,
    source_mode: str,
) -> list[str]:
    tips = [
        "热门景区与博物馆的预约要求、开放时间请以官方渠道为准，本系统不提供预约结论。",
    ]
    if source_mode == "amap_live":
        tips.append("名称、地址、坐标与可获取的参考价来自高德实时 POI；未提供的价格标注为未获取，0 元不代表免费。")
    else:
        tips.append("当前使用内置示例地点库，票价与营业时间为演示数据，不是实时报价。")
    bad_days = [day for day in weather_days if not day.outdoor_ok]
    if bad_days:
        dates = "、".join(day.date.strftime("%m-%d") for day in bad_days[:3])
        tips.append(f"{dates} 天气条件不适合长时间户外，已优先安排室内景点，建议携带雨具并保留机动时间。")
    if draft.origin and draft.origin != draft.destination:
        tips.append("往返大交通按距离规则估算，建议尽早购票并预留到站/值机时间。")
    tips.append("市内路线耗时按直线距离、固定速度与缓冲时间估算，不是实时路网导航；出发前请核对地图路线。")
    if budget.budget_status == "over_budget":
        if budget.adjustments:
            tips.append(
                f"估算总花费超出预算 {budget.over_budget_amount:.0f} 元，"
                f"已优先调整未指定的交通或住宿以控制开支，必去地点与明确偏好未改动。"
            )
        else:
            tips.append(
                f"估算总花费超出预算 {budget.over_budget_amount:.0f} 元，"
                "系统未自动改写已确认偏好，可按预算建议进一步调整。"
            )
    if draft.must_visit:
        tips.append("必去地点已优先纳入；若遇闭馆或核验失败，会在结果中明确标注，不会虚构可访问状态。")
    return tips[:5]


def iter_generation(session_id: str, cancel_event: Event | None = None) -> Generator[dict, None, None]:
    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

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

    total = len(STAGE_META)

    # 1. 需求理解（复核）
    if cancelled():
        return
    run = yield from run_stage("requirement", "需求理解", 1, total, lambda: requirement_stage(analysis))
    trace.append(run.trace)

    # 2. 资料检索
    if cancelled():
        return
    run = yield from run_stage("research", "资料检索", 2, total, lambda: research_stage(draft))
    research = cast(ResearchResult, run.outcome.value)
    trace.append(run.trace)

    # 3. 天气
    if cancelled():
        return
    run = yield from run_stage("weather", "天气适配", 3, total, lambda: weather_stage(draft))
    weather = cast(WeatherOutcome, run.outcome.value)
    weather_days = weather.days
    trace.append(run.trace)

    # 4. 地点核验
    if cancelled():
        return
    run = yield from run_stage("verification", "地点核验", 4, total, lambda: verification_stage(draft))
    verification = cast(VerificationResult, run.outcome.value)
    warnings.extend(run.outcome.warnings or [])
    trace.append(run.trace)
    verified_attractions = [item.place for item in verification.places if item.place.category == "attraction"]
    if not verified_attractions:
        yield {"event": "error", "data": {"message": "没有通过核验的景点，无法生成行程。", "details": verification.warnings}}
        return

    # 5. 路线编排
    if cancelled():
        return
    run = yield from run_stage("route", "路线编排", 5, total, lambda: route_stage(draft, verification, weather_days))
    route = cast(RouteOutcome, run.outcome.value)
    days = route.days
    warnings.extend(run.outcome.warnings or [])
    trace.append(run.trace)

    # 6. 预算
    if cancelled():
        return
    run = yield from run_stage("budget", "预算计算", 6, total, lambda: budget_stage(draft, days, verification))
    budget = cast(BudgetPlan, run.outcome.value)
    assumptions.extend(run.outcome.assumptions or [])
    trace.append(run.trace)

    if budget.budget_status == "over_budget":
        warnings.append(
            f"预算上限 {budget.limit_total:.0f} 元，估算总花费 {budget.total:.0f} 元，"
            f"超出预算 {budget.over_budget_amount:.0f} 元"
            f"（{budget.tolerance_amount:.0f} 元容差，{'已控制在容差内' if budget.within_tolerance else '仍超过容差'}）。"
        )
    if budget.completeness == "partial":
        warnings.append(
            f"高德未提供{'、'.join(budget.unknown_prices)}价格，预算仅汇总已获取费用；0 元不代表免费，请按实际报价核实。"
        )

    # 文案与来源
    facts = "\n".join(
        [
            f"目的地：{draft.destination}；日期：{draft.start_date} 至 {draft.end_date}；人数：{draft.travelers}；节奏：{draft.pace}",
            f"预算：{'已知费用合计' if budget.completeness == 'partial' else '估算总花费'} {budget.total:.0f} 元，人均 {budget.per_person:.0f} 元，状态 {budget.budget_status}",
            *[
                f"第{day.day_index}天 {day.date} {day.theme}：" + "、".join(item.name for item in day.items if item.kind == "attraction")
                for day in days
            ],
        ]
    )
    fallback_summary = _default_summary(
        draft, len(days), budget.total, budget.per_person, budget.completeness
    )
    llm_summary = generate_summary(facts, fallback_summary)
    if cancelled():
        return
    llm_used = llm_summary is not None
    summary = llm_summary or fallback_summary
    tips = _build_tips(draft, weather_days, budget, verification.source_mode)

    demo_mode, data_mode_label = _source_label(
        verification, weather_days, llm_used, bool(research.snippets)
    )
    sources: list[SourceRef] = []
    if verification.source_mode == "amap_live":
        sources.append(
            SourceRef(
                type="place",
                title="高德地图 POI 检索",
                detail="名称、地址、坐标来自高德；营业时间与参考价仅在字段存在时采用，未提供的价格标注为未获取。",
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
                title="和风天气预报",
                detail="按行程跨度选择可用的预报接口；超出预报范围时回退历史气候参考。",
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
    if verification.source_mode == "amap_live":
        budget_detail = "餐饮/住宿优先采用高德返回的参考价，未提供的价格不计为 0 元；交通与早餐按规则估算。"
    else:
        budget_detail = "按示例票价、餐饮均价、住宿示例价与交通规则计算，不是实时报价。"
    sources.append(
        SourceRef(
            type="budget",
            title="规则估算",
            detail=budget_detail,
            level="rule_estimate",
            verified=False,
        )
    )
    if llm_used:
        sources.append(
            SourceRef(type="llm", title="大模型文案", detail="仅使用已给出的结构化事实润色概述，不生成提示或外部数据。", level="llm", verified=False)
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
    if cancelled():
        return
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
