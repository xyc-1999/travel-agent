from __future__ import annotations

from datetime import date, timedelta

from app.data_loader import normalize_city_name
from app.models import PlaceCheck, Place, RequirementInput, VerificationResult
from app.services.place_source import load_places


def _closed_on(place: Place, day: date) -> bool:
    return day.isoweekday() in place.closed_weeks


def _name_matches(requested: str, place: Place) -> bool:
    left = normalize_city_name(requested).replace("景区", "").replace("公园", "")
    right = place.name.replace("景区", "").replace("公园", "")
    if not left:
        return False
    if left in right or right in left:
        return True
    return any(tag and tag in left for tag in place.tags)


AVOID_ALIASES = {
    "辣": ["川菜", "火锅", "川味", "麻辣"],
    "爬山": ["徒步", "登山"],
}


def _avoid_hit(place: Place, avoid: list[str]) -> str | None:
    haystack = " ".join([place.name, place.description, *place.tags, *place.cuisine])
    for item in avoid:
        keyword = item.strip()
        if keyword and keyword in haystack:
            return keyword
        for alias in AVOID_ALIASES.get(keyword, []):
            if alias in haystack:
                return keyword
    return None


def _to_verified(place: Place, source_mode: str) -> PlaceCheck:
    checks = ["城市一致", "坐标存在"]
    warnings: list[str] = []
    if source_mode == "demo_catalog":
        checks.extend(["开放时间已记录（示例值）", "票价/价格已记录（示例值）"])
        warnings.append("来自内置示例地点库，只完成一致性校验，不代表实时营业状态。")
    else:
        checks.extend(
            [
                "名称/地址/坐标来自高德",
                "参考价来自高德字段" if place.price_known else "价格未获取（0 元不代表免费）",
                "营业时间来自高德字段" if place.hours_known else "营业时间未获取",
            ]
        )
    if not place.address:
        warnings.append("缺少详细地址。")
    return PlaceCheck(place=place, level=("catalog_checked" if source_mode == "demo_catalog" else "live_poi"), checks=checks, warnings=warnings)


def run_verification(draft: RequirementInput) -> VerificationResult:
    city = normalize_city_name(draft.destination)
    snapshot = load_places(city, draft.lodging)
    if snapshot.source_mode == "unavailable":
        return VerificationResult(warnings=snapshot.warnings, source_mode=snapshot.source_mode)

    source_mode = snapshot.source_mode
    warnings: list[str] = list(snapshot.warnings)
    places: list[PlaceCheck] = []
    for place in snapshot.places:
        hit = _avoid_hit(place, draft.avoid)
        if hit:
            warnings.append(f"已按“不去/避开 {hit}”排除地点：{place.name}")
            continue
        places.append(_to_verified(place, source_mode))

    if not places:
        return VerificationResult(warnings=warnings, source_mode=source_mode)

    if source_mode == "amap_live":
        unknown_hours = [check.place for check in places if not check.place.hours_known]
        if unknown_hours:
            warnings.append(f"高德未提供 {len(unknown_hours)} 个地点的营业时间，时间轴按常规时段假设。")

    days: list[date] = []
    if draft.start_date and draft.end_date:
        current = draft.start_date
        while current <= draft.end_date:
            days.append(current)
            current += timedelta(days=1)

    must_visit_ids: list[str] = []
    unresolved: list[str] = []
    unavailable: list[str] = []
    attractions = [item.place for item in places if item.place.category == "attraction"]
    for requested in draft.must_visit:
        match = next((place for place in attractions if _name_matches(requested, place)), None)
        if not match:
            unresolved.append(requested)
            warnings.append(f"必去地点“{requested}”未能在已核验地点库中匹配，未纳入行程。")
            continue
        if days and all(_closed_on(match, day) for day in days):
            unavailable.append(requested)
            warnings.append(
                f"必去地点“{match.name}”在所选日期的常规闭馆日覆盖全部行程日，未纳入固定安排；"
                "请调整日期或与场馆确认临时开放安排。"
            )
            continue
        if match.id not in must_visit_ids:
            must_visit_ids.append(match.id)

    attractions = [item.place for item in places if item.place.category == "attraction"]
    if len(attractions) < 4:
        warnings.append("可用景点少于 4 个，行程可能偏空。")

    return VerificationResult(
        places=places,
        must_visit_ids=must_visit_ids,
        unresolved_must_visit=unresolved,
        unavailable_must_visit=unavailable,
        warnings=_dedupe(warnings),
        source_mode=source_mode,
    )


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
