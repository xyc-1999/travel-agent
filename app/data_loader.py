from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from app.config import DATA_DIR, SUPPORTED_CITIES


def _read_json(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def cities_data() -> dict:
    return _read_json("cities.json")


@lru_cache(maxsize=1)
def places_data() -> dict:
    return _read_json("places.json")


@lru_cache(maxsize=1)
def climate_data() -> dict:
    return _read_json("climate.json")


def supported_cities() -> list[str]:
    return list(SUPPORTED_CITIES)


def normalize_city_name(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"[\s,，。;；]", "", str(value))
    text = text.replace("市", "")
    return text


def city_info(name: str | None) -> dict | None:
    normalized = normalize_city_name(name)
    return cities_data()["cities"].get(normalized)


def city_coordinates(name: str | None) -> tuple[float, float] | None:
    info = city_info(name)
    if not info:
        return None
    return float(info["lat"]), float(info["lng"])


def city_province(name: str | None) -> str | None:
    info = city_info(name)
    return info.get("province") if info else None


def province_candidates(name: str | None) -> list[str] | None:
    normalized = normalize_city_name(name)
    return cities_data()["provinces"].get(normalized)


def is_supported_destination(name: str | None) -> bool:
    return normalize_city_name(name) in SUPPORTED_CITIES


@lru_cache(maxsize=1)
def all_places() -> list[dict]:
    return list(places_data()["places"])


def places_for_city(city: str) -> list[dict]:
    normalized = normalize_city_name(city)
    return [place for place in all_places() if place["city"] == normalized]


def places_by_category(city: str, category: str) -> list[dict]:
    return [place for place in places_for_city(city) if place["category"] == category]


def find_place(place_id: str) -> dict | None:
    for place in all_places():
        if place["id"] == place_id:
            return place
    return None


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return round(2 * radius * math.asin(math.sqrt(a)), 2)


def place_distance(a: dict, b: dict) -> float:
    return haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])


@lru_cache(maxsize=1)
def knowledge_chunks() -> list[dict]:
    chunks: list[dict] = []
    knowledge_dir = DATA_DIR / "knowledge"
    for path in sorted(knowledge_dir.glob("*.md")):
        city = path.stem
        text = path.read_text(encoding="utf-8")
        sections = re.split(r"\n##\s+", text)
        for index, section in enumerate(sections):
            title_line, _, body = section.partition("\n")
            title = title_line.strip().lstrip("#").strip() or f"片段 {index + 1}"
            body = body.strip()
            if not body:
                continue
            chunks.append(
                {
                    "city": city,
                    "source": f"app/data/knowledge/{path.name}",
                    "title": f"{city} · {title}",
                    "text": body,
                }
            )
    return chunks


def climate_for(city: str) -> dict | None:
    return climate_data()["cities"].get(normalize_city_name(city))


def knowledge_dir() -> Path:
    return DATA_DIR / "knowledge"
