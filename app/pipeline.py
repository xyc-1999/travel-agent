from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Generator, Literal

from app.agents.budget import run_budget
from app.agents.research import run_research
from app.agents.route import run_route_planning
from app.agents.verification import run_verification
from app.models import (
    AgentTrace,
    BudgetPlan,
    DayPlan,
    RequirementAnalysis,
    RequirementInput,
    ResearchResult,
    VerificationResult,
    WeatherDay,
)
from app.services.weather import build_weather


STAGE_META: list[tuple[str, str]] = [
    ("requirement", "需求理解"),
    ("research", "资料检索"),
    ("weather", "天气适配"),
    ("verification", "地点核验"),
    ("route", "路线编排"),
    ("budget", "预算计算"),
]


@dataclass
class StageOutcome:
    """单个 Agent 阶段的统一产出，供编排器消费。"""

    value: Any
    summary: str
    details: list[str]
    status: Literal["done", "warning", "error"] = "done"
    warnings: list[str] | None = None
    assumptions: list[str] | None = None


@dataclass
class StageRun:
    outcome: StageOutcome
    trace: AgentTrace


@dataclass
class WeatherOutcome:
    days: list[WeatherDay]
    provider: str


@dataclass
class RouteOutcome:
    days: list[DayPlan]
    adaptations: list[str]


def run_stage(
    agent: str,
    label: str,
    index: int,
    total: int,
    compute: Callable[[], StageOutcome],
) -> Generator[dict, None, StageRun]:
    """执行一个阶段并产出统一的 agent_start / agent_done 事件。"""
    start = time.perf_counter()
    yield {"event": "agent_start", "data": {"agent": agent, "label": label, "index": index, "total": total}}
    outcome = compute()
    trace = AgentTrace(
        agent=agent,
        label=label,
        status=outcome.status,
        summary=outcome.summary,
        details=outcome.details,
        duration_ms=int((time.perf_counter() - start) * 1000),
    )
    yield {"event": "agent_done", "data": trace.model_dump(mode="json")}
    return StageRun(outcome=outcome, trace=trace)


def requirement_stage(analysis: RequirementAnalysis) -> StageOutcome:
    details = [analysis.summary]
    details.extend(analysis.assumptions[:3])
    return StageOutcome(
        value=analysis,
        summary="已完成结构化需求与冲突检查",
        details=details,
        assumptions=list(analysis.assumptions),
    )


def research_stage(draft: RequirementInput) -> StageOutcome:
    research = run_research(draft)
    details = [f"{snippet.title}（匹配分 {snippet.score}）" for snippet in research.snippets]
    if not research.snippets:
        details.append(research.note)
    return StageOutcome(
        value=research,
        summary=f"检索到 {len(research.snippets)} 条示例攻略片段",
        details=details,
        status="warning" if not research.snippets else "done",
    )


def weather_stage(draft: RequirementInput) -> StageOutcome:
    days, provider = build_weather(
        draft.destination or "",
        draft.start_date,  # type: ignore[arg-type]
        draft.end_date,  # type: ignore[arg-type]
        draft.demo_weather,
    )
    bad_days = [day for day in days if not day.outdoor_ok]
    details = [
        f"{day.date.strftime('%m-%d')}：{day.condition}，{day.low}~{day.high}℃，降水概率 {day.rain_probability}%（{day.source}）"
        for day in days
    ]
    return StageOutcome(
        value=WeatherOutcome(days=days, provider=provider),
        summary=f"{len(days)} 天天气约束，{len(bad_days)} 天不适合长时间户外",
        details=details,
        status="warning" if provider != "qweather_forecast" else "done",
    )


def verification_stage(draft: RequirementInput) -> StageOutcome:
    verification = run_verification(draft)
    attractions = [item.place.name for item in verification.places if item.place.category == "attraction"]
    details = [
        f"可用地点 {len(verification.places)} 个（景点 {len(attractions)}）",
        f"核验来源：{verification.source_mode}",
    ]
    if verification.must_visit_ids:
        details.append(f"必去地点已核验 {len(verification.must_visit_ids)} 个")
    details.extend(verification.warnings[:4])
    status: Literal["done", "warning", "error"] = (
        "error" if not attractions else ("warning" if verification.warnings else "done")
    )
    return StageOutcome(
        value=verification,
        summary="完成地点匹配与开放日校验",
        details=details,
        status=status,
        warnings=list(verification.warnings),
    )


def route_stage(
    draft: RequirementInput,
    verification: VerificationResult,
    weather_days: list[WeatherDay],
) -> StageOutcome:
    days, route_warnings, adaptations = run_route_planning(draft, verification, weather_days)
    attraction_count = sum(1 for day in days for item in day.items if item.kind == "attraction")
    details = [
        f"编排 {len(days)} 天，{attraction_count} 个景点",
        f"天气适配 {len(adaptations)} 处",
    ]
    details.extend(adaptations[:3])
    return StageOutcome(
        value=RouteOutcome(days=days, adaptations=adaptations),
        summary="完成按天分组、就近排序与时间轴编排",
        details=details,
        status="warning" if route_warnings else "done",
        warnings=list(route_warnings),
    )


def budget_stage(
    draft: RequirementInput,
    days: list[DayPlan],
    verification: VerificationResult,
) -> StageOutcome:
    budget = run_budget(draft, days, verification)
    details = [
        f"总估算 {budget.total:.0f} 元，人均 {budget.per_person:.0f} 元",
        f"预算状态：{budget.budget_status}",
    ]
    if budget.limit_total is not None:
        details.append(f"预算上限 {budget.limit_total:.0f} 元，容差 {budget.tolerance_amount:.0f} 元")
    if budget.over_budget_amount > 0:
        details.append(f"超出预算 {budget.over_budget_amount:.0f} 元（{'容差内' if budget.within_tolerance else '超过容差'}）")
    if budget.completeness == "partial":
        details.append(f"未获取价格：{'、'.join(budget.unknown_prices)}（0 元不代表免费，未计入总估算）")
    details.extend(budget.adjustments)
    details.extend(budget.suggestions[:3])
    return StageOutcome(
        value=budget,
        summary="完成分项预算与预算容差校验" + ("，部分价格未获取" if budget.completeness == "partial" else ""),
        details=details,
        status="warning" if budget.budget_status == "over_budget" or budget.adjustments or budget.completeness == "partial" else "done",
        assumptions=list(budget.assumptions),
    )
