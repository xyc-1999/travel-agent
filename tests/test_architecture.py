from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import Place
from app.pipeline import StageOutcome, run_stage
from app.services import place_source
from app.storage import list_trips, save_trip


def test_stage_runner_emits_start_then_done_with_trace():
    events: list[dict] = []

    def compute() -> StageOutcome:
        return StageOutcome(value={"ok": True}, summary="完成", details=["细节"])

    generator = run_stage("demo", "演示阶段", 1, 2, compute)
    events.append(next(generator))
    assert events[0]["event"] == "agent_start"
    assert events[0]["data"]["index"] == 1

    events.append(next(generator))
    assert events[1]["event"] == "agent_done"
    assert events[1]["data"]["status"] == "done"

    with pytest.raises(StopIteration) as stopped:
        next(generator)
    run = stopped.value.value
    assert run.trace.agent == "demo"
    assert run.trace.duration_ms >= 0
    assert run.outcome.value == {"ok": True}


def test_supported_city_loads_catalog_without_warnings():
    snapshot = place_source.load_places("成都")

    assert snapshot.source_mode == "demo_catalog"
    assert snapshot.warnings == []
    assert any(place.category == "attraction" for place in snapshot.places)


def test_unsupported_city_without_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(place_source.config, "AMAP_API_KEY", None)

    snapshot = place_source.load_places("桂林")

    assert snapshot.source_mode == "unavailable"
    assert snapshot.places == []
    assert any("演示库仅覆盖" in warning for warning in snapshot.warnings)


def test_unsupported_city_uses_live_poi_adapter(monkeypatch):
    monkeypatch.setattr(place_source.config, "AMAP_API_KEY", "test-key")
    calls: list[str] = []

    def fake_search(city: str, category: str, limit: int = 12, keyword: str | None = None):
        calls.append(category)
        place = Place(
            id=f"amap-{category}",
            city=city,
            name=f"高德-{category}",
            category=category,  # type: ignore[arg-type]
            lat=30.0,
            lng=110.0,
        )
        return [place], None

    monkeypatch.setattr(place_source, "search_amap_places", fake_search)
    snapshot = place_source.load_places("桂林")

    assert snapshot.source_mode == "amap_live"
    assert calls[:3] == ["attraction", "food", "hotel"]
    assert {place.category for place in snapshot.places} == {"attraction", "food", "hotel"}
    assert any("少于 4 个" in warning for warning in snapshot.warnings)


def test_hotel_search_uses_lodging_preference(monkeypatch):
    monkeypatch.setattr(place_source.config, "AMAP_API_KEY", "test-key")
    keywords: list[str | None] = []

    def fake_search(city: str, category: str, limit: int = 12, keyword: str | None = None):
        keywords.append(keyword if category == "hotel" else None)
        place = Place(
            id=f"amap-{category}-{len(keywords)}",
            city=city,
            name=f"高德-{category}",
            category=category,  # type: ignore[arg-type]
            lat=30.0,
            lng=110.0,
        )
        return [place], None

    monkeypatch.setattr(place_source, "search_amap_places", fake_search)
    place_source.load_places("桂林", "经济型")

    assert keywords[2] == "经济型酒店"


def test_list_trips_total_is_not_capped_by_limit(sample_itinerary):
    for index in range(3):
        copy = sample_itinerary.model_copy(
            update={
                "trip_id": f"paging-test-{index}",
                "created_at": sample_itinerary.created_at + timedelta(minutes=index),
            }
        )
        save_trip(copy)

    page = list_trips(limit=2)

    assert page.total >= 3
    assert len(page.items) == 2
