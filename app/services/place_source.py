from __future__ import annotations

from dataclasses import dataclass

from app import config
from app.data_loader import is_supported_destination, places_for_city, supported_cities
from app.models import Place
from app.services.amap import search_amap_places


@dataclass(frozen=True)
class PlaceSnapshot:
    """一次地点加载的统一结果，隐藏示例库与实时 POI 的差异。"""

    places: list[Place]
    source_mode: str
    warnings: list[str]


def load_places(city: str) -> PlaceSnapshot:
    """按城市加载地点：内置示例库优先，其次高德实时 POI，最后明确不可用。"""
    if is_supported_destination(city):
        places = [Place.model_validate(item) for item in places_for_city(city)]
        return PlaceSnapshot(places=places, source_mode="demo_catalog", warnings=[])

    if config.AMAP_API_KEY:
        places: list[Place] = []
        warnings: list[str] = []
        for category in ("attraction", "food", "hotel"):
            fetched, error = search_amap_places(city, category, limit=14 if category == "attraction" else 10)
            if error:
                warnings.append(error)
            places.extend(fetched)
        if len([place for place in places if place.category == "attraction"]) < 4:
            warnings.append("高德候选景点少于 4 个，无法保证可靠编排。")
        return PlaceSnapshot(places=places, source_mode="amap_live", warnings=warnings)

    supported = "、".join(supported_cities())
    return PlaceSnapshot(
        places=[],
        source_mode="unavailable",
        warnings=[f"当前环境没有“{city}”的地点数据；演示库仅覆盖{supported}。"],
    )
