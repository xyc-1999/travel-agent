from __future__ import annotations

import math

from app.data_loader import city_coordinates, haversine_km, normalize_city_name
from app.models import (
    BudgetLine,
    BudgetPlan,
    DayPlan,
    Place,
    RequirementInput,
    VerificationResult,
)


def _intercity_estimate(draft: RequirementInput) -> tuple[float, str]:
    origin = normalize_city_name(draft.origin)
    destination = normalize_city_name(draft.destination)
    if not origin or not destination or origin == destination:
        return 0.0, "未计入跨城交通（出发地缺失或与目的地相同）。"
    origin_coord = city_coordinates(origin)
    destination_coord = city_coordinates(destination)
    if not origin_coord or not destination_coord:
        return 0.0, "出发地或目的地坐标缺失，未计入跨城交通。"

    distance = haversine_km(origin_coord[0], origin_coord[1], destination_coord[0], destination_coord[1])
    mode = draft.intercity_mode
    if not mode or mode == "不限":
        mode = "高铁" if distance <= 1200 else "飞机"
    travelers = draft.travelers or 1
    vehicles = max(1, math.ceil(travelers / 5))

    if mode == "飞机":
        one_way = max(350.0, distance * 0.8 + 80) * travelers
        detail = f"{mode} · 约 {distance:.0f} km · 往返 · 按经济舱规则估算"
    elif mode == "自驾":
        one_way = (distance * 1.15) * vehicles
        detail = f"{mode} · 约 {distance:.0f} km · 往返 · {vehicles} 辆车（油费+路桥估算）"
    else:
        one_way = max(80.0, distance * 0.46) * travelers
        detail = f"{mode} · 约 {distance:.0f} km · 往返 · 按二等座规则估算"

    return round(one_way * 2, 2), detail


def run_budget(
    draft: RequirementInput,
    days: list[DayPlan],
    verification: VerificationResult,
) -> BudgetPlan:
    travelers = draft.travelers or 1
    rooms = max(1, math.ceil(travelers / 2))
    day_count = len(days)

    ticket_total = sum(
        item.cost * travelers
        for day in days
        for item in day.items
        if item.kind == "attraction"
    )
    meal_total = sum(
        item.cost * travelers
        for day in days
        for item in day.items
        if item.kind == "meal"
    )
    breakfast_total = 25 * travelers * day_count
    hotel_items = [item for day in days for item in day.items if item.kind == "hotel"]
    hotel_total = sum(item.cost * rooms for item in hotel_items)
    local_transport = sum(leg.cost for day in days for leg in day.legs)
    intercity_total, intercity_detail = _intercity_estimate(draft)

    subtotal = ticket_total + meal_total + breakfast_total + hotel_total + local_transport + intercity_total
    other_total = round(subtotal * 0.08, 2)

    lines = [
        BudgetLine(key="intercity", label="往返大交通", amount=round(intercity_total, 2), detail=intercity_detail),
        BudgetLine(
            key="hotel",
            label="住宿",
            amount=round(hotel_total, 2),
            detail=f"{len(hotel_items)} 晚 × {rooms} 间" if hotel_items else "未安排住宿",
        ),
        BudgetLine(
            key="meals",
            label="餐饮",
            amount=round(meal_total + breakfast_total, 2),
            detail=f"含正餐与早餐/饮水（{travelers} 人 × {day_count} 天）",
        ),
        BudgetLine(key="tickets", label="门票", amount=round(ticket_total, 2), detail="按已核验景点的示例票价"),
        BudgetLine(key="local_transport", label="市内交通", amount=round(local_transport, 2), detail="按实际编排路线估算"),
        BudgetLine(key="other", label="机动与其他", amount=other_total, detail="按小计 8% 预留"),
    ]
    total = round(sum(line.amount for line in lines), 2)
    per_person = round(total / travelers, 2) if travelers else total

    limit_total: float | None = None
    assumptions: list[str] = ["票价、餐饮与住宿均来自示例地点库或规则估算，不是实时价格。"]
    if draft.budget.amount is not None and draft.budget.basis:
        if draft.budget.basis == "per_person":
            limit_total = round(draft.budget.amount * travelers, 2)
            assumptions.append(f"人均预算 {draft.budget.amount:.0f} 元 × {travelers} 人 = 总上限 {limit_total:.0f} 元。")
        else:
            limit_total = round(draft.budget.amount, 2)
            assumptions.append(f"总预算上限 {limit_total:.0f} 元。")
    if not draft.origin:
        assumptions.append("未提供出发地，往返大交通按 0 元计，实际总花费可能更高。")

    if limit_total is None:
        status = "no_budget"
        remaining = None
        over = 0.0
        suggestions: list[str] = ["未设置预算上限，可在结果中查看人均估算。"]
    elif total <= limit_total:
        status = "ok"
        remaining = round(limit_total - total, 2)
        over = 0.0
        suggestions = []
        if remaining < limit_total * 0.1:
            suggestions.append(f"预算余量仅 {remaining:.0f} 元（不足 10%），建议预留机动费用。")
    else:
        status = "over_budget"
        remaining = round(limit_total - total, 2)
        over = round(total - limit_total, 2)
        suggestions = _over_budget_suggestions(draft, days, verification, rooms, over)

    return BudgetPlan(
        lines=lines,
        total=total,
        per_person=per_person,
        limit_total=limit_total,
        budget_status=status,
        remaining=remaining,
        over_budget_amount=over,
        suggestions=suggestions,
        assumptions=assumptions,
    )


def _over_budget_suggestions(
    draft: RequirementInput,
    days: list[DayPlan],
    verification: VerificationResult,
    rooms: int,
    over: float,
) -> list[str]:
    suggestions: list[str] = []
    hotels = [item.place for item in verification.places if item.place.category == "hotel"]
    hotel_item = next((item for day in days for item in day.items if item.kind == "hotel"), None)
    nights = sum(1 for day in days for item in day.items if item.kind == "hotel")
    if hotel_item and hotels:
        cheapest = min(hotels, key=lambda hotel: hotel.price_per_night or 99999)
        if cheapest.price_per_night and hotel_item.cost > cheapest.price_per_night:
            savings = (hotel_item.cost - cheapest.price_per_night) * rooms * nights
            if savings > 0:
                suggestions.append(
                    f"住宿改选更经济方案（如「{cheapest.name}」），约可省 {savings:.0f} 元。"
                )

    tickets = sorted(
        [item for day in days for item in day.items if item.kind == "attraction" and item.cost > 0],
        key=lambda item: item.cost,
        reverse=True,
    )
    if tickets:
        top = tickets[0]
        savings = top.cost * (draft.travelers or 1)
        suggestions.append(f"少安排一个付费景点（如「{top.name}」），约可省 {savings:.0f} 元。")

    meal_total = sum(item.cost for day in days for item in day.items if item.kind == "meal") * (draft.travelers or 1)
    if meal_total > 0:
        suggestions.append(f"餐饮降档或减少一顿正餐，约可省 {meal_total * 0.22:.0f} 元。")

    if len(days) > 1:
        last_day = days[-1]
        day_cost = (
            sum(item.cost for item in last_day.items if item.kind in {"attraction", "meal"}) * (draft.travelers or 1)
            + 25 * (draft.travelers or 1)
        )
        suggestions.append(f"缩短 1 天行程，约可省 {day_cost:.0f} 元（不含返程交通变化）。")

    if over > 0:
        transparency = f"当前估算超出预算 {over:.0f} 元，以上为可选调整，不会自动更改你的住宿或兴趣偏好。"
        return (suggestions[:3] + [transparency])
    return suggestions[:4]
