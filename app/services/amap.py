from __future__ import annotations

import httpx

from app import config
from app.models import Place


CATEGORY_CONFIG = {
    "attraction": {"types": "110000", "keywords": "景点"},
    "food": {"types": "050000", "keywords": "美食"},
    "hotel": {"types": "100000", "keywords": "酒店"},
}


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
        open_time="00:00",
        close_time="23:59",
        closed_weeks=[],
        duration_minutes=120 if category == "attraction" else (90 if category == "food" else 0),
        ticket_price=0.0,
        avg_price=0.0,
        price_per_night=0.0,
        level="",
        cuisine=[],
        description=f"高德实时 POI（{poi_type or '类型未知'}）；营业时间、票价与价格未核验。",
        source="amap_live",
    )


def search_amap_places(city: str, category: str, limit: int = 12) -> tuple[list[Place], str | None]:
    if not config.AMAP_API_KEY:
        return [], "未配置 AMAP_API_KEY"
    cfg = CATEGORY_CONFIG[category]
    try:
        with httpx.Client(timeout=config.AMAP_TIMEOUT_SECONDS) as client:
            response = client.get(
                "https://restapi.amap.com/v3/place/text",
                params={
                    "key": config.AMAP_API_KEY,
                    "keywords": cfg["keywords"],
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
