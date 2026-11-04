from __future__ import annotations

from datetime import date, timedelta

from app.data_loader import haversine_km, normalize_city_name
from app.models import (
    DayPlan,
    Place,
    RequirementInput,
    ScheduleItem,
    TransportLeg,
    VerificationResult,
    WeatherDay,
)


SPEED_KMH = {"公共交通": 18, "打车": 25, "步行": 4.5, "包车": 30}
START_TIME = {"轻松": 9 * 60 + 30, "适中": 9 * 60, "紧凑": 8 * 60 + 30}
SPOTS_PER_DAY = {"轻松": 1, "适中": 2, "紧凑": 3}


def _minutes_text(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _text_minutes(text: str) -> int:
    try:
        hour, minute = text.split(":", 1)
        return int(hour) * 60 + int(minute)
    except (ValueError, AttributeError):
        return 9 * 60


def _travel(current: Place | None, target: Place, mode: str, travelers: int) -> tuple[TransportLeg, int]:
    if current is None:
        distance = 0.0
        minutes = 0
        cost = 0.0
    else:
        distance = haversine_km(current.lat, current.lng, target.lat, target.lng) * 1.35
        speed = SPEED_KMH.get(mode, 18)
        minutes = max(5, round(distance / speed * 60) + 8)
        if mode == "步行":
            cost = 0.0
        elif mode == "打车":
            cost = 10 + distance * 2.2
        elif mode == "包车":
            cost = 30 + distance * 3.8
        else:
            cost = (2 + distance * 0.3) * travelers
    leg = TransportLeg(
        mode=mode,
        from_place=current.name if current else "起点",
        to_place=target.name,
        distance_km=round(distance, 2),
        minutes=minutes,
        cost=round(cost, 2),
        cost_basis="per_group",
    )
    return leg, minutes


def _open_on(place: Place, day: date) -> bool:
    return day.isoweekday() not in place.closed_weeks


def _interest_score(place: Place, interests: list[str]) -> float:
    score = 0.0
    for interest in interests:
        if interest in place.tags or interest in place.description:
            score += 3.0
        elif interest == "美食" and place.category == "food":
            score += 2.0
    return score


def _distance_score(place: Place, anchor: Place | None) -> float:
    if anchor is None:
        return 0.0
    distance = haversine_km(place.lat, place.lng, anchor.lat, anchor.lng)
    return max(0.0, 8.0 - distance / 2.0)


def _choose_attractions(
    draft: RequirementInput,
    attractions: list[Place],
    must_visit_ids: list[str],
    day: date,
    weather: WeatherDay,
    used: set[str],
    hotel: Place | None,
) -> list[Place]:
    available = [place for place in attractions if place.id not in used and _open_on(place, day)]
    target = SPOTS_PER_DAY.get(draft.pace or "适中", 3)
    required_must = [place for place in attractions if place.id in must_visit_ids and place.id not in used and _open_on(place, day)]
    selected = required_must[:target]

    scored: list[tuple[float, Place]] = []
    for place in available:
        if place.id in must_visit_ids:
            continue
        score = _interest_score(place, draft.interests) + _distance_score(place, hotel)
        if not weather.outdoor_ok:
            score += 6.0 if place.indoor else -4.0
        if weather.high >= 33:
            score += 2.0 if place.indoor else -1.0
        scored.append((score, place))
    scored.sort(key=lambda item: item[0], reverse=True)

    selected_ids = {place.id for place in selected}
    for _, place in scored:
        if len(selected) >= target:
            break
        if place.id not in selected_ids:
            selected.append(place)
            selected_ids.add(place.id)

    if not weather.outdoor_ok and selected:
        outdoor = [place for place in selected if not place.indoor and place.id not in must_visit_ids]
        indoor_available = [place for place in available if place.indoor and place.id not in {p.id for p in selected}]
        if outdoor and indoor_available and len(outdoor) >= len(selected) - 1:
            replacement = indoor_available[0]
            selected[selected.index(outdoor[-1])] = replacement
    return selected


def _order_places(places: list[Place], hotel: Place | None) -> list[Place]:
    if not places:
        return []
    remaining = list(places)
    current = hotel
    ordered: list[Place] = []
    while remaining:
        if current is None:
            chosen = remaining.pop(0)
        else:
            chosen = min(remaining, key=lambda place: haversine_km(current.lat, current.lng, place.lat, place.lng))  # type: ignore[arg-type]
            remaining.remove(chosen)
        ordered.append(chosen)
        current = chosen
    return ordered


def _food_near(foods: list[Place], anchor: Place | None, used: set[str], avoid: list[str]) -> Place | None:
    candidates = [
        food
        for food in foods
        if food.id not in used
        and not any(keyword and keyword in " ".join([food.name, food.description, *food.cuisine]) for keyword in avoid)
    ]
    if not candidates:
        candidates = [food for food in foods if food.id not in used]
    if not candidates:
        return None
    if anchor is None:
        return candidates[0]
    return min(candidates, key=lambda food: haversine_km(anchor.lat, anchor.lng, food.lat, food.lng))


def _hotel_for(draft: RequirementInput, hotels: list[Place]) -> Place | None:
    if not hotels:
        return None
    preferred = draft.lodging or "舒适型"
    exact = [hotel for hotel in hotels if hotel.level == preferred]
    if exact:
        return exact[0]
    if preferred == "民宿":
        inns = [hotel for hotel in hotels if "民宿" in hotel.tags or "民宿" in hotel.name]
        if inns:
            return inns[0]
    return hotels[0]


def run_route_planning(
    draft: RequirementInput,
    verification: VerificationResult,
    weather_days: list[WeatherDay],
) -> tuple[list[DayPlan], list[str], list[str]]:
    city = normalize_city_name(draft.destination)
    places = [item.place for item in verification.places]
    attractions = [place for place in places if place.category == "attraction"]
    foods = [place for place in places if place.category == "food"]
    hotels = [place for place in places if place.category == "hotel"]
    hotel = _hotel_for(draft, hotels)
    must_ids_set = set(verification.must_visit_ids)

    day_count = draft.days or len(weather_days)
    weather_by_date = {item.date: item for item in weather_days}
    used_attractions: set[str] = set()
    used_food: set[str] = set()
    days: list[DayPlan] = []
    warnings: list[str] = []
    adaptations: list[str] = []

    must_remaining = [place for place in attractions if place.id in verification.must_visit_ids]
    # 必去地点优先分配到仍开放的日期
    must_by_day: dict[date, list[Place]] = {day.date: [] for day in weather_days}
    for place in must_remaining:
        candidates = [day.date for day in weather_days if _open_on(place, day.date)]
        if not candidates:
            continue
        chosen_day = min(candidates, key=lambda value: len(must_by_day.get(value, [])))
        must_by_day[chosen_day].append(place)

    for index in range(day_count):
        day_date = draft.start_date + timedelta(days=index) if draft.start_date else weather_days[index].date
        weather = weather_by_date.get(day_date) or weather_days[index]
        used_before = set(used_attractions)
        must_for_day = [place for place in must_by_day.get(day_date, []) if place.id not in used_attractions]
        must_ids = [place.id for place in must_for_day]

        selected = _choose_attractions(draft, attractions, must_ids, day_date, weather, used_attractions, hotel)
        ordered = _order_places(selected, hotel)
        used_attractions.update(place.id for place in ordered)
        for place in must_for_day:
            if place.id not in {item.id for item in ordered}:
                ordered.insert(0, place)
                used_attractions.add(place.id)

        day_adaptations: list[str] = []
        if not weather.outdoor_ok and ordered:
            outdoor = [place for place in ordered if not place.indoor and place.id not in must_ids_set]
            unused_indoor = [place for place in attractions if place.indoor and place.id not in used_attractions and _open_on(place, day_date)]
            if outdoor and unused_indoor:
                replacement = unused_indoor[0]
                shown = outdoor[0]
                ordered[ordered.index(shown)] = replacement
                used_attractions.add(replacement.id)
                used_attractions.discard(shown.id)
                day_adaptations.append(f"天气不适户外：将「{shown.name}」替换为室内「{replacement.name}」。")
            elif outdoor:
                day_adaptations.append(f"天气不适户外，但可用室内替代不足，保留「{outdoor[0].name}」并建议缩短停留。")
            elif any(not place.indoor for place in ordered):
                day_adaptations.append("天气不适户外，但当天其余候选均为必去地点，保留安排并建议携带雨具。")

        # 早上/下午分组
        midpoint = (len(ordered) + 1) // 2
        morning = ordered[:midpoint]
        afternoon = ordered[midpoint:]
        start_minutes = START_TIME.get(draft.pace or "适中", 9 * 60)
        current_minutes = start_minutes
        current_place: Place | None = None
        items: list[ScheduleItem] = []
        legs: list[TransportLeg] = []
        scheduling_notes: list[str] = []

        for place in morning:
            leg, travel_minutes = _travel(current_place, place, draft.local_transport or "公共交通", draft.travelers)
            if current_place is not None:
                legs.append(leg)
            current_minutes += travel_minutes
            visit_start = current_minutes
            close_minutes = _text_minutes(place.close_time)
            if visit_start >= close_minutes:
                scheduling_notes.append(f"「{place.name}」到达时已超过闭馆时间，未安排进当天时间轴。")
                if legs and legs[-1].to_place == place.name:
                    legs.pop()
                current_minutes -= travel_minutes
                continue
            visit_end = min(visit_start + place.duration_minutes, close_minutes)
            if visit_end - visit_start < place.duration_minutes:
                scheduling_notes.append(
                    f"「{place.name}」因闭馆时间限制，停留缩短为 {visit_end - visit_start} 分钟。"
                )
            items.append(
                ScheduleItem(
                    kind="attraction",
                    name=place.name,
                    place_id=place.id,
                    start_time=_minutes_text(visit_start),
                    end_time=_minutes_text(visit_end),
                    duration_minutes=max(30, visit_end - visit_start),
                    cost=place.ticket_price,
                    cost_basis="per_person",
                    note=place.description,
                    address=place.address,
                    lat=place.lat,
                    lng=place.lng,
                    indoor=place.indoor,
                    must_visit=place.id in must_ids_set,
                    verification_level=next(
                        (item.level for item in verification.places if item.place.id == place.id),
                        "catalog_checked",
                    ),
                    tags=place.tags,
                )
            )
            current_minutes = visit_end
            current_place = place

        # 午餐
        lunch_anchor = current_place or hotel
        lunch = _food_near(foods, lunch_anchor, used_food, draft.avoid)
        lunch_start = max(12 * 60, current_minutes)
        if lunch_start > current_minutes:
            items.append(
                ScheduleItem(
                    kind="buffer",
                    name="机动/前往午餐",
                    start_time=_minutes_text(current_minutes),
                    end_time=_minutes_text(lunch_start),
                    duration_minutes=lunch_start - current_minutes,
                    note="预留步行与排队时间。",
                )
            )
        if lunch:
            used_food.add(lunch.id)
            if lunch_anchor:
                leg, travel_minutes = _travel(lunch_anchor, lunch, draft.local_transport or "公共交通", draft.travelers)
                legs.append(leg)
                lunch_start += travel_minutes
            lunch_end = lunch_start + max(60, lunch.duration_minutes)
            items.append(
                ScheduleItem(
                    kind="meal",
                    name=lunch.name,
                    place_id=lunch.id,
                    start_time=_minutes_text(lunch_start),
                    end_time=_minutes_text(lunch_end),
                    duration_minutes=lunch_end - lunch_start,
                    cost=lunch.avg_price,
                    cost_basis="per_person",
                    note=f"午餐 · {lunch.description}",
                    address=lunch.address,
                    lat=lunch.lat,
                    lng=lunch.lng,
                    indoor=True,
                    verification_level=next(
                        (item.level for item in verification.places if item.place.id == lunch.id),
                        "catalog_checked",
                    ),
                    tags=lunch.tags,
                )
            )
            current_minutes = lunch_end
            current_place = lunch
        else:
            lunch_end = lunch_start + 60
            items.append(
                ScheduleItem(
                    kind="meal",
                    name="午餐自理（未安排具体餐厅）",
                    start_time=_minutes_text(lunch_start),
                    end_time=_minutes_text(lunch_end),
                    duration_minutes=60,
                    cost=60,
                    cost_basis="per_person",
                    note="地点库中没有可用餐厅，按人均 60 元估算，不虚构店名。",
                )
            )
            current_minutes = lunch_end

        for place in afternoon:
            leg, travel_minutes = _travel(current_place, place, draft.local_transport or "公共交通", draft.travelers)
            legs.append(leg)
            current_minutes += travel_minutes
            visit_start = current_minutes
            close_minutes = _text_minutes(place.close_time)
            if visit_start >= close_minutes:
                scheduling_notes.append(f"「{place.name}」到达时已超过闭馆时间，未安排进当天时间轴。")
                if legs and legs[-1].to_place == place.name:
                    legs.pop()
                current_minutes -= travel_minutes
                continue
            visit_end = min(visit_start + place.duration_minutes, close_minutes)
            if visit_end - visit_start < place.duration_minutes:
                scheduling_notes.append(
                    f"「{place.name}」因闭馆时间限制，停留缩短为 {visit_end - visit_start} 分钟。"
                )
            items.append(
                ScheduleItem(
                    kind="attraction",
                    name=place.name,
                    place_id=place.id,
                    start_time=_minutes_text(visit_start),
                    end_time=_minutes_text(visit_end),
                    duration_minutes=max(30, visit_end - visit_start),
                    cost=place.ticket_price,
                    cost_basis="per_person",
                    note=place.description,
                    address=place.address,
                    lat=place.lat,
                    lng=place.lng,
                    indoor=place.indoor,
                    must_visit=place.id in must_ids_set,
                    verification_level=next(
                        (item.level for item in verification.places if item.place.id == place.id),
                        "catalog_checked",
                    ),
                    tags=place.tags,
                )
            )
            current_minutes = visit_end
            current_place = place

        # 晚餐
        dinner_anchor = current_place or hotel
        dinner = _food_near(foods, dinner_anchor, used_food, draft.avoid)
        dinner_start = max(18 * 60, current_minutes)
        if dinner_start > current_minutes:
            items.append(
                ScheduleItem(
                    kind="buffer",
                    name="自由活动",
                    start_time=_minutes_text(current_minutes),
                    end_time=_minutes_text(dinner_start),
                    duration_minutes=dinner_start - current_minutes,
                    note="按节奏预留休息时间。",
                )
            )
        if dinner:
            used_food.add(dinner.id)
            if dinner_anchor:
                leg, travel_minutes = _travel(dinner_anchor, dinner, draft.local_transport or "公共交通", draft.travelers)
                legs.append(leg)
                dinner_start += travel_minutes
            dinner_end = dinner_start + max(60, dinner.duration_minutes)
            items.append(
                ScheduleItem(
                    kind="meal",
                    name=dinner.name,
                    place_id=dinner.id,
                    start_time=_minutes_text(dinner_start),
                    end_time=_minutes_text(dinner_end),
                    duration_minutes=dinner_end - dinner_start,
                    cost=dinner.avg_price,
                    cost_basis="per_person",
                    note=f"晚餐 · {dinner.description}",
                    address=dinner.address,
                    lat=dinner.lat,
                    lng=dinner.lng,
                    indoor=True,
                    verification_level=next(
                        (item.level for item in verification.places if item.place.id == dinner.id),
                        "catalog_checked",
                    ),
                    tags=dinner.tags,
                )
            )
            current_minutes = dinner_end
            current_place = dinner
        else:
            dinner_end = dinner_start + 60
            items.append(
                ScheduleItem(
                    kind="meal",
                    name="晚餐自理（未安排具体餐厅）",
                    start_time=_minutes_text(dinner_start),
                    end_time=_minutes_text(dinner_end),
                    duration_minutes=60,
                    cost=70,
                    cost_basis="per_person",
                    note="地点库中没有可用餐厅，按人均 70 元估算，不虚构店名。",
                )
            )
            current_minutes = dinner_end

        if hotel and index < day_count - 1:
            items.append(
                ScheduleItem(
                    kind="hotel",
                    name=hotel.name,
                    place_id=hotel.id,
                    start_time="21:00",
                    end_time="次日 08:00",
                    duration_minutes=0,
                    cost=hotel.price_per_night,
                    cost_basis="per_room",
                    note=f"住宿 · {hotel.level or '未标注档次'}（每间每晚示例价）",
                    address=hotel.address,
                    lat=hotel.lat,
                    lng=hotel.lng,
                    indoor=True,
                    verification_level=next(
                        (item.level for item in verification.places if item.place.id == hotel.id),
                        "catalog_checked",
                    ),
                    tags=hotel.tags,
                )
            )

        tag_counts: dict[str, int] = {}
        for place in ordered:
            for tag in place.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        theme_tags = [tag for tag, _ in sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:2]]
        theme = " · ".join(theme_tags) if theme_tags else "城市漫步"

        notes: list[str] = [weather.advice, weather.note]
        if not weather.is_forecast and weather.source == "climate_reference":
            notes.append("天气为历史气候参考，不是预报；出发前请复核。")
        notes.extend(day_adaptations)
        notes.extend(scheduling_notes)
        scheduled_ids = {item.place_id for item in items if item.kind == "attraction" and item.place_id}
        for place in must_for_day:
            if place.id in scheduled_ids:
                notes.append(f"已按必去要求安排「{place.name}」。")
            else:
                warnings.append(
                    f"必去地点「{place.name}」未能排入 {day_date.isoformat()} 的可行时间轴，请调整节奏或日期。"
                )
        if day_adaptations:
            adaptations.extend(day_adaptations)
        if current_minutes > 22 * 60:
            notes.append("当天结束时间较晚，可减少一个景点或打车返程。")

        days.append(
            DayPlan(
                day_index=index + 1,
                date=day_date,
                theme=theme,
                weather=weather,
                items=items,
                legs=legs,
                notes=notes,
                weather_adaptation=day_adaptations,
            )
        )

    if not attractions:
        warnings.append("没有可用景点，无法生成有效行程。")
    if not hotels:
        warnings.append("没有可用住宿数据，预算未包含住宿。")
    if not foods:
        warnings.append("没有可用餐饮数据，餐食按通用标准估算。")
    return days, warnings, adaptations


def route_coordinates(day: DayPlan) -> list[tuple[float, float]]:
    return [(item.lat, item.lng) for item in day.items if item.lat is not None and item.lng is not None]
