from __future__ import annotations

import re

import httpx

from app import config
from app.models import Place


CATEGORY_CONFIG = {
    "attraction": {"types": "110000", "keywords": "景点"},
    "food": {"types": "050000", "keywords": "美食"},
    "hotel": {"types": "100000", "keywords": "酒店"},
}

ROUTE_ENDPOINTS = {
    "打车": "https://restapi.amap.com/v3/direction/driving",
    "包车": "https://restapi.amap.com/v3/direction/driving",
    "步行": "https://restapi.amap.com/v3/direction/walking",
    "公共交通": "https://restapi.amap.com/v3/direction/transit/integrated",
}

# 按用户住宿偏好分别检索，避免不同偏好得到同一批酒店
HOTEL_KEYWORDS = {
    "经济型": "经济型酒店",
    "舒适型": "舒适型酒店",
    "豪华型": "豪华酒店",
    "民宿": "民宿",
}

_TIME_PATTERN = re.compile(r"(\d{1,2}):(\d{2})")
_WEEKDAY_INDEX = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}


def _hotel_level(poi_type: str, name: str) -> str:
    """仅依据高德 POI 类型/名称中明确出现的星级或酒店类型判断档次，不臆测。"""
    text = f"{poi_type} {name}"
    if any(word in text for word in ("五星", "5星", "四星", "4星")):
        return "豪华型"
    if any(word in text for word in ("三星", "3星")):
        return "舒适型"
    if any(word in text for word in ("二星", "2星", "经济型", "连锁")):
        return "经济型"
    if "民宿" in text:
        return "民宿"
    return ""


def _parse_closed_weeks(raw: str) -> list[int]:
    """从“周一闭馆/每周三休息”等文本中提取常规闭馆日，无法判断时返回空列表。"""
    text = raw.strip()
    if not text or not any(word in text for word in ("闭馆", "休息", "不开放", "停业")):
        return []
    weeks = {
        _WEEKDAY_INDEX[char]
        for char in re.findall(r"周([一二三四五六日天])", text)
        if char in _WEEKDAY_INDEX
    }
    return sorted(weeks)


def _to_price(value: object) -> float | None:
    """只接受高德返回的数字价格；缺失、0 元或无法解析时视为未获取。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"[]", "None", "0"}:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    price = float(match.group(0))
    return price if price > 0 else None


def _parse_hours(raw: str) -> tuple[str, str] | None:
    """从高德营业时间文本中提取可用的起止时间。"""
    text = raw.strip()
    if not text:
        return None
    if "24小时" in text or "00:00-24:00" in text.replace(" ", ""):
        return "00:00", "23:59"
    matches = _TIME_PATTERN.findall(text)
    if not matches:
        return None
    start_hour, start_minute = int(matches[0][0]), int(matches[0][1])
    end_hour, end_minute = int(matches[-1][0]), int(matches[-1][1])
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    if end_total <= start_total:
        end_total = 23 * 60 + 59
    end_total = min(end_total, 23 * 60 + 59)
    return f"{start_total // 60:02d}:{start_total % 60:02d}", f"{end_total // 60:02d}:{end_total % 60:02d}"


def _build_place(city: str, poi: dict, category: str) -> Place | None:
    location = str(poi.get("location") or "")
    if "," not in location:
        return None
    lng_text, lat_text = location.split(",", 1)
    try:
        lng, lat = float(lng_text), float(lat_text)
    except ValueError:
        return None
    name = str(poi.get("name") or "").strip()
    if not name:
        return None
    poi_type = str(poi.get("type") or "")
    indoor = any(word in poi_type for word in ("博物馆", "展览", "商场", "剧院", "图书馆"))
    biz = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
    raw_hours = str(biz.get("open_time") or biz.get("opentime2") or "").strip()
    hours = _parse_hours(raw_hours)

    price: float | None = None
    if category == "food":
        price = _to_price(biz.get("cost"))
    elif category == "hotel":
        price = _to_price(biz.get("lowest_price")) or _to_price(biz.get("cost"))
    price_known = price is not None

    if hours:
        open_time, close_time = hours
        hours_known = True
    else:
        open_time, close_time = "00:00", "23:59"
        hours_known = False

    notes = [f"高德实时 POI（{poi_type or '类型未知'}）"]
    if raw_hours:
        notes.append(f"高德营业时间：{raw_hours}")
    else:
        notes.append("高德未提供营业时间，时间轴按常规时段计划，出发前请核实")
    closed_weeks = _parse_closed_weeks(raw_hours)
    if category == "hotel":
        if price_known:
            notes.append(f"高德参考最低价 {price:.0f} 元/间/晚（具体房型以预订平台为准）")
        else:
            notes.append("高德未提供房型与房价，0 元不代表免费，预算未计入")
    elif price_known:
        label = {"food": "参考人均"}.get(category, "参考消费")
        notes.append(f"高德{label} {price:.0f} 元")
    else:
        notes.append("高德未提供价格/票价，0 元不代表免费，需自行核实")
    notes.append("请以商家或景区当日公告为准")

    return Place(
        id=f"amap-{poi.get('id', name)}",
        city=city,
        name=name,
        category=category,  # type: ignore[arg-type]
        tags=[tag for tag in poi_type.split(";") if tag][:4],
        indoor=indoor,
        lat=lat,
        lng=lng,
        address=f"{str(poi.get('pname') or '')}{str(poi.get('cityname') or '')}{str(poi.get('adname') or '')}{str(poi.get('address') or '')}",
        open_time=open_time,
        close_time=close_time,
        closed_weeks=closed_weeks,
        duration_minutes=120 if category == "attraction" else (90 if category == "food" else 0),
        ticket_price=0.0,
        avg_price=(price or 0.0) if category == "food" else 0.0,
        price_per_night=(price or 0.0) if category == "hotel" else 0.0,
        level=_hotel_level(poi_type, name) if category == "hotel" else "",
        cuisine=[],
        description="；".join(notes),
        source="amap_live",
        price_known=price_known,
        hours_known=hours_known,
    )


def search_amap_places(
    city: str,
    category: str,
    limit: int = 12,
    keyword: str | None = None,
) -> tuple[list[Place], str | None]:
    if not config.AMAP_API_KEY:
        return [], "未配置 AMAP_API_KEY"
    cfg = CATEGORY_CONFIG[category]
    try:
        with httpx.Client(timeout=config.AMAP_TIMEOUT_SECONDS) as client:
            response = client.get(
                "https://restapi.amap.com/v3/place/text",
                params={
                    "key": config.AMAP_API_KEY,
                    "keywords": keyword or cfg["keywords"],
                    "types": cfg["types"],
                    "city": city,
                    "citylimit": "true",
                    "offset": str(min(25, limit)),
                    "page": "1",
                    "extensions": "all",
                },
            )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return [], f"高德 POI 请求失败：{type(exc).__name__}"
    if payload.get("status") != "1":
        return [], f"高德 POI 返回错误：{payload.get('info', 'unknown')}"

    places: list[Place] = []
    seen: set[str] = set()
    for poi in payload.get("pois") or []:
        place = _build_place(city, poi, category)
        if not place or place.name in seen:
            continue
        seen.add(place.name)
        places.append(place)
        if len(places) >= limit:
            break
    return places, None


def route_amap_places(
    origin: tuple[float, float],
    destination: tuple[float, float],
    mode: str,
    city: str,
) -> tuple[float, int, float, str, list[tuple[float, float]]] | None:
    """返回真实路线的距离、耗时、费用、摘要和道路折线；失败时返回 None。"""
    if not config.AMAP_API_KEY or mode not in ROUTE_ENDPOINTS:
        return None
    endpoint = ROUTE_ENDPOINTS[mode]
    params = {
        "key": config.AMAP_API_KEY,
        "origin": f"{origin[1]:.6f},{origin[0]:.6f}",
        "destination": f"{destination[1]:.6f},{destination[0]:.6f}",
    }
    if mode == "公共交通":
        params.update({"city": city, "strategy": "0", "alternative_route": "0"})
    try:
        with httpx.Client(timeout=config.AMAP_ROUTE_TIMEOUT_SECONDS) as client:
            response = client.get(endpoint, params=params)
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if payload.get("status") != "1":
        return None
    route = payload.get("route") or {}
    candidates = route.get("paths") if mode != "公共交通" else route.get("transits")
    if not candidates:
        return None
    selected = candidates[0]
    try:
        distance = float(selected.get("distance", 0)) / 1000
        minutes = max(1, round(float(selected.get("duration", 0)) / 60))
    except (TypeError, ValueError):
        return None
    if distance <= 0 or minutes <= 0:
        return None
    geometry: list[tuple[float, float]] = []
    for step in selected.get("steps") or []:
        raw_polyline = str(step.get("polyline") or "")
        for point in raw_polyline.split(";"):
            try:
                lng_text, lat_text = point.split(",", 1)
                geometry.append((float(lat_text), float(lng_text)))
            except (ValueError, TypeError):
                continue
    if mode == "公共交通":
        for transit in selected.get("segments") or []:
            for walking in transit.get("walking", {}).get("steps", []):
                for point in str(walking.get("polyline") or "").split(";"):
                    try:
                        lng_text, lat_text = point.split(",", 1)
                        geometry.append((float(lat_text), float(lng_text)))
                    except (ValueError, TypeError):
                        continue
    toll = selected.get("taxi_cost") if mode in {"打车", "包车"} else selected.get("tolls")
    toll = toll or selected.get("tolls") or selected.get("cost") or 0
    try:
        cost = max(0.0, float(toll))
    except (TypeError, ValueError):
        cost = 0.0
    summary = "高德实时路线"
    if mode == "公共交通":
        summary = "高德公交/地铁路线"
    elif mode == "步行":
        summary = "高德步行路线"
    else:
        summary = "高德驾车路线"
    return round(distance, 2), minutes, round(cost, 2), summary, geometry
