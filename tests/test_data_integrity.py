from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents.budget import run_budget
from app.agents.route import run_route_planning
from app.models import (
    BudgetSpec,
    Place,
    PlaceCheck,
    RequirementInput,
    VerificationResult,
    WeatherDay,
)
from app.services.amap import _build_place, _parse_closed_weeks, _parse_hours, _to_price
from app.services.llm import _is_safe_summary


def _poi(**biz_ext) -> dict:
    return {
        "id": "B0TEST",
        "name": "测试地点",
        "type": "餐饮服务;中餐厅",
        "location": "113.0,28.2",
        "pname": "湖南省",
        "cityname": "长沙市",
        "adname": "岳麓区",
        "address": "测试路 1 号",
        "biz_ext": biz_ext,
    }


def test_amap_place_uses_real_price_and_hours():
    place = _build_place("长沙", _poi(cost="86.00", open_time="10:00-22:00"), "food")

    assert place is not None
    assert place.price_known is True
    assert place.avg_price == pytest.approx(86.0)
    assert place.hours_known is True
    assert place.open_time == "10:00"
    assert place.close_time == "22:00"
    assert "高德参考人均 86 元" in place.description


def test_amap_place_marks_missing_price_instead_of_zero():
    place = _build_place("长沙", _poi(), "hotel")

    assert place is not None
    assert place.price_known is False
    assert place.price_per_night == 0.0
    assert place.hours_known is False
    assert "0 元不代表免费" in place.description
    assert "未提供营业时间" in place.description


def test_amap_place_parses_lowest_price_for_hotel():
    place = _build_place("长沙", _poi(lowest_price="328"), "hotel")

    assert place is not None
    assert place.price_known is True
    assert place.price_per_night == pytest.approx(328.0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10:00-22:00", ("10:00", "22:00")),
        ("08:30-17:30;周一闭馆", ("08:30", "17:30")),
        ("24小时营业", ("00:00", "23:59")),
        ("", None),
        ("营业时间待定", None),
    ],
)
def test_parse_hours(raw, expected):
    assert _parse_hours(raw) == expected


def test_parse_closed_weeks_only_from_explicit_text():
    assert _parse_closed_weeks("08:30-17:30;周一闭馆") == [1]
    assert _parse_closed_weeks("10:00-22:00") == []
    assert _parse_closed_weeks("") == []


@pytest.mark.parametrize("value", ["", "[]", "0", "0.00", None, "暂无"])
def test_to_price_rejects_missing_values(value):
    assert _to_price(value) is None


def test_safe_summary_blocks_fabricated_rules():
    assert _is_safe_summary("岳麓书院每日限流，建议提前官网预约。") is False
    assert _is_safe_summary("橘子洲门票免费，最佳游览时间傍晚。") is False
    assert _is_safe_summary("长沙 4 天 3 人行程，围绕美食与历史安排，节奏适中。") is True


def _place(category: str, *, known: bool, price: float = 0.0) -> PlaceCheck:
    place = Place(
        id=f"amap-{category}",
        city="长沙",
        name=f"高德-{category}",
        category=category,  # type: ignore[arg-type]
        lat=28.2,
        lng=113.0,
        ticket_price=0.0,
        avg_price=price if category == "food" else 0.0,
        price_per_night=price if category == "hotel" else 0.0,
        price_known=known,
        hours_known=known,
        source="amap_live",
    )
    return PlaceCheck(place=place, level="live_poi")


def test_budget_marks_partial_when_prices_missing():
    start = date(2026, 12, 1)
    draft = RequirementInput(
        origin="上海",
        destination="长沙",
        start_date=start,
        end_date=start + timedelta(days=1),
        travelers=3,
        budget=BudgetSpec(amount=9000, basis="total"),
        local_transport="公共交通",
        pace="适中",
    )
    verification = VerificationResult(
        places=[
            _place("attraction", known=False),
            _place("attraction", known=False),
            _place("food", known=True, price=80),
            _place("hotel", known=False),
        ],
        source_mode="amap_live",
    )
    weather_days = [
        WeatherDay(date=start, high=18, low=10),
        WeatherDay(date=start + timedelta(days=1), high=17, low=9),
    ]

    days, _warnings, _adaptations = run_route_planning(draft, verification, weather_days)
    budget = run_budget(draft, days, verification)

    assert budget.completeness == "partial"
    assert set(budget.unknown_prices) == {"门票", "住宿"}
    assert budget.assumptions[-1].startswith("未获取价格")
    hotel_line = next(line for line in budget.lines if line.key == "hotel")
    assert "房价未获取" in hotel_line.detail
    ticket_line = next(line for line in budget.lines if line.key == "tickets")
    assert "0 元不代表免费" in ticket_line.detail
    meal_line = next(line for line in budget.lines if line.key == "meals")
    assert meal_line.amount > 0
