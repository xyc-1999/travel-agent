from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.agents.requirement import analyze_requirement
from app.agents.verification import run_verification
from app.models import BudgetSpec, RequirementInput
from app.orchestrator import generate_itinerary
from app.orchestrator import _build_tips, _source_label
from app.services.weather import build_weather
from app.storage import save_session


def _ready_itinerary(payload: RequirementInput):
    analysis = analyze_requirement(payload, session_id=uuid.uuid4().hex[:12])
    assert analysis.status == "ready", analysis.questions
    save_session(analysis.session_id, payload, analysis)
    return generate_itinerary(analysis.session_id)


def test_mixed_weather_sources_are_explicitly_labeled():
    from app.models import VerificationResult, WeatherDay

    demo, label = _source_label(
        VerificationResult(source_mode="amap_live"),
        [
            WeatherDay(date=date(2026, 12, 1), high=20, low=10, is_forecast=True),
            WeatherDay(date=date(2026, 12, 2), high=19, low=9, is_forecast=False),
        ],
        llm_used=False,
        has_guides=False,
    )

    assert demo is True
    assert "和风天气预报 + 历史气候参考" in label


def test_route_tip_discloses_approximate_travel_time():
    from app.models import BudgetPlan, WeatherDay

    tips = _build_tips(
        RequirementInput(destination="成都"),
        [WeatherDay(date=date(2026, 12, 1), high=20, low=10)],
        BudgetPlan(),
        "demo_catalog",
    )

    assert any("直线距离" in tip and "不是实时路网导航" in tip for tip in tips)


def test_end_to_end_itinerary_is_structurally_valid(complete_payload: RequirementInput):
    itinerary = _ready_itinerary(complete_payload)

    assert itinerary.destination == "成都"
    assert itinerary.days_count == 3
    assert len(itinerary.days) == 3
    assert itinerary.demo_mode is True
    assert itinerary.agent_trace, "必须保留多 Agent 执行轨迹"
    assert {trace.agent for trace in itinerary.agent_trace} == {
        "requirement",
        "research",
        "weather",
        "verification",
        "route",
        "budget",
    }

    for day in itinerary.days:
        assert day.items, "每天至少要有可执行安排"
        assert any(item.kind == "attraction" for item in day.items)
        start_minutes = 0
        for item in day.items:
            if item.kind == "hotel":
                continue
            start, end = item.start_time, item.end_time
            if "次日" in end:
                continue
            hour, minute = start.split(":")
            current = int(hour) * 60 + int(minute)
            hour, minute = end.split(":")
            finish = int(hour) * 60 + int(minute)
            assert finish >= current, f"时间轴倒挂：{item.name} {start}-{end}"
            assert current >= start_minutes, "行程顺序必须按时间递增"
            start_minutes = current
        assert any(item.kind == "meal" for item in day.items)

    budget = itinerary.budget
    assert budget.total > 0
    assert round(sum(line.amount for line in budget.lines), 2) == round(budget.total, 2)
    assert round(budget.per_person, 2) == round(budget.total / itinerary.travelers, 2)
    assert any("示例" in source.title or "示例" in source.detail for source in itinerary.sources)
    assert all(source.level != "live" for source in itinerary.sources)


def test_rainy_demo_triggers_weather_adaptation(complete_payload: RequirementInput):
    payload = complete_payload.model_copy(update={"demo_weather": "rainy"})
    itinerary = _ready_itinerary(payload)

    assert all(day.weather.source == "demo_forced" for day in itinerary.days)
    assert all(not day.weather.outdoor_ok for day in itinerary.days)

    for day in itinerary.days:
        outdoor = [item for item in day.items if item.kind == "attraction" and not item.indoor]
        if outdoor:
            assert day.weather_adaptation, f"第 {day.day_index} 天有户外安排但未记录适配说明"


def test_must_visit_is_scheduled_and_marked(complete_payload: RequirementInput):
    from app.export import build_markdown

    payload = complete_payload.model_copy(update={"must_visit": ["杜甫草堂"]})
    itinerary = _ready_itinerary(payload)

    items = [item for day in itinerary.days for item in day.items if item.kind == "attraction"]
    matches = [item for item in items if item.name == "杜甫草堂"]
    assert len(matches) == 1, "必去地点应恰好安排一次"
    assert matches[0].must_visit is True
    assert any(
        "已按必去要求安排「杜甫草堂」" in note
        for day in itinerary.days
        for note in day.notes
    )
    assert "【必去】杜甫草堂" in build_markdown(itinerary)


def test_rainy_weather_keeps_must_visit(complete_payload: RequirementInput):
    payload = complete_payload.model_copy(update={"must_visit": ["杜甫草堂"], "demo_weather": "rainy"})
    itinerary = _ready_itinerary(payload)

    attractions = [item for day in itinerary.days for item in day.items if item.kind == "attraction"]
    must = [item for item in attractions if item.name == "杜甫草堂"]
    assert must and must[0].must_visit is True
    assert any(day.weather_adaptation for day in itinerary.days)


def test_over_budget_is_transparent_and_does_not_rewrite_preferences(complete_payload: RequirementInput):
    payload = complete_payload.model_copy(
        update={"budget": BudgetSpec(amount=600, basis="total"), "travelers": 2}
    )
    itinerary = _ready_itinerary(payload)

    assert itinerary.budget.budget_status == "over_budget"
    assert itinerary.budget.over_budget_amount > 0
    assert itinerary.budget.suggestions
    assert any("不会自动更改" in suggestion for suggestion in itinerary.budget.suggestions)
    assert itinerary.request_snapshot.lodging == "舒适型"
    assert any("超出预算" in warning for warning in itinerary.warnings)


def test_over_budget_reports_exact_amount_and_tolerance(complete_payload: RequirementInput):
    from app.export import build_markdown

    payload = complete_payload.model_copy(
        update={
            "intercity_mode": "飞机",
            "lodging": "豪华型",
            "budget": BudgetSpec(amount=3000, basis="total"),
        }
    )
    itinerary = _ready_itinerary(payload)
    budget = itinerary.budget

    assert budget.budget_status == "over_budget"
    assert budget.tolerance_amount == 500
    assert budget.within_tolerance is False
    assert round(budget.total - budget.limit_total, 2) == budget.over_budget_amount
    assert any(
        "超出预算" in warning and f"{budget.over_budget_amount:.0f} 元" in warning
        for warning in itinerary.warnings
    )
    markdown = build_markdown(itinerary)
    assert "超出预算" in markdown
    assert f"{budget.over_budget_amount:.0f} 元" in markdown


def test_budget_fitting_keeps_plan_within_tolerance(complete_payload: RequirementInput):
    payload = complete_payload.model_copy(
        update={
            "intercity_mode": None,
            "lodging": None,
            "budget": BudgetSpec(amount=5000, basis="total"),
        }
    )
    itinerary = _ready_itinerary(payload)
    budget = itinerary.budget

    assert budget.total <= budget.limit_total + budget.tolerance_amount
    assert budget.over_budget_amount > 0
    assert budget.within_tolerance is True
    assert budget.original_total > budget.total
    assert budget.adjustments
    assert "原方案" in budget.adjustments[0]
    assert any("往返交通" in item for item in budget.adjustments)
    assert any("住宿" in item for item in budget.adjustments)
    hotel_items = [item for day in itinerary.days for item in day.items if item.kind == "hotel"]
    assert all(item.cost <= 300 for item in hotel_items)


def test_per_person_budget_is_multiplied_for_limit(complete_payload: RequirementInput):
    payload = complete_payload.model_copy(
        update={"budget": BudgetSpec(amount=2500, basis="per_person"), "travelers": 2}
    )
    itinerary = _ready_itinerary(payload)

    assert itinerary.budget.limit_total == 5000
    assert itinerary.budget.per_person > 0


def test_no_budget_does_not_claim_over_budget(complete_payload: RequirementInput):
    payload = complete_payload.model_copy(
        update={"budget": BudgetSpec(amount=None, basis=None, skip=True)}
    )
    itinerary = _ready_itinerary(payload)

    assert itinerary.budget.budget_status == "no_budget"
    assert itinerary.budget.limit_total is None


def test_verification_reports_unresolved_must_visit(complete_payload: RequirementInput):
    payload = complete_payload.model_copy(update={"must_visit": ["不存在的景点"]})
    result = run_verification(payload)

    assert result.source_mode == "demo_catalog"
    assert result.unresolved_must_visit == ["不存在的景点"]
    assert any("未能在已核验地点库中匹配" in warning for warning in result.warnings)


def test_avoid_lists_filter_food_by_alias(complete_payload: RequirementInput):
    payload = complete_payload.model_copy(update={"avoid": ["辣"]})
    result = run_verification(payload)

    food_names = [item.place.name for item in result.places if item.place.category == "food"]
    assert food_names
    assert all("火锅" not in name and "麻婆" not in name for name in food_names)
    assert any("不去/避开 辣" in warning for warning in result.warnings)


def test_weather_reference_is_climate_not_forecast(trip_start: date):
    days, provider = build_weather("成都", trip_start, trip_start + timedelta(days=2), "auto")

    assert provider == "climate_reference"
    assert len(days) == 3
    assert all(day.is_forecast is False for day in days)
    assert all(day.source == "climate_reference" for day in days)


def test_unsupported_city_verification_is_explicit():
    payload = RequirementInput(
        destination="拉萨",
        start_date=date.today() + timedelta(days=20),
        end_date=date.today() + timedelta(days=22),
        travelers=2,
    )
    result = run_verification(payload)

    assert result.source_mode == "unavailable"
    assert result.places == []
    assert any("没有“拉萨”的地点数据" in warning for warning in result.warnings)
