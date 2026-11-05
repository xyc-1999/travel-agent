from __future__ import annotations

import httpx

from app import config
from app.services import amap, llm, weather


def test_amap_timeout_returns_provider_error(monkeypatch):
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")

    def fail(*args, **kwargs):
        raise httpx.TimeoutException("private transport details")

    monkeypatch.setattr(amap.httpx, "Client", lambda **kwargs: _Client(fail))
    places, error = amap.search_amap_places("成都", "attraction")

    assert places == []
    assert error == "高德 POI 请求失败：TimeoutException"
    assert "private transport details" not in error


def test_weather_http_failure_falls_back_to_empty_forecast(monkeypatch, trip_start):
    monkeypatch.setattr(config, "QWEATHER_API_KEY", "test-key")
    monkeypatch.setattr(weather, "_weather_location", lambda _city: "104.06,30.67")
    monkeypatch.setattr(weather.httpx, "Client", lambda **kwargs: _Client(lambda *a, **k: _Response(503)))

    assert weather._qweather_forecast("成都", trip_start, trip_start) == {}


def test_llm_malformed_response_uses_no_summary(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: _Client(lambda *a, **k: _Response(200, {"unexpected": True})))

    assert llm.generate_summary("facts", "fallback") is None


def test_amap_route_adapter_parses_driving_route(monkeypatch):
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")
    response = _Response(200, {"status": "1", "route": {"paths": [{"distance": "2400", "duration": "900", "tolls": "6"}]}})
    monkeypatch.setattr(amap.httpx, "Client", lambda **kwargs: _Client(lambda *a, **k: response))

    result = amap.route_amap_places((30.0, 104.0), (30.02, 104.02), "打车", "成都")

    assert result == (2.4, 15, 6.0, "高德驾车路线", [])


def test_amap_route_adapter_extracts_step_polylines(monkeypatch):
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")
    response = _Response(200, {
        "status": "1",
        "route": {"paths": [{
            "distance": "2400", "duration": "900", "steps": [
                {"polyline": "104.0000,30.0000;104.0100,30.0100"},
                {"polyline": "104.0100,30.0100;104.0200,30.0200"},
            ],
        }]},
    })
    monkeypatch.setattr(amap.httpx, "Client", lambda **kwargs: _Client(lambda *a, **k: response))

    result = amap.route_amap_places((30, 104), (30.02, 104.02), "打车", "成都")

    assert result is not None
    assert result[4] == [(30.0, 104.0), (30.01, 104.01), (30.01, 104.01), (30.02, 104.02)]


def test_amap_route_adapter_returns_none_for_malformed_route(monkeypatch):
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")
    response = _Response(200, {"status": "1", "route": {"paths": []}})
    monkeypatch.setattr(amap.httpx, "Client", lambda **kwargs: _Client(lambda *a, **k: response))

    assert amap.route_amap_places((30.0, 104.0), (30.02, 104.02), "步行", "成都") is None


def test_route_leg_uses_live_route_and_reports_its_duration(monkeypatch):
    from app.agents import route as route_agent
    from app.models import Place

    monkeypatch.setattr(route_agent, "route_amap_places", lambda *args: (3.2, 11, 8.0, "高德驾车路线", [(30.0, 104.0), (30.05, 104.05)]))
    origin = Place(id="a", city="成都", name="A", category="attraction", lat=30.0, lng=104.0)
    destination = Place(id="b", city="成都", name="B", category="attraction", lat=30.1, lng=104.1)

    leg, minutes = route_agent._travel(origin, destination, "打车", 2)

    assert leg.source == "amap_route"
    assert leg.distance_km == 3.2
    assert leg.cost == 8.0
    assert leg.cost_basis == "per_group"
    assert leg.geometry == [(30.0, 104.0), (30.05, 104.05)]
    assert minutes == 11


def test_public_transport_cost_is_per_person_in_budget():
    from datetime import date

    from app.agents.budget import _compose_budget
    from app.models import DayPlan, RequirementInput, TransportLeg, WeatherDay

    plan_day = DayPlan(
        day_index=1,
        date=date(2026, 12, 1),
        theme="",
        weather=WeatherDay(date=date(2026, 12, 1), high=20, low=10),
        legs=[TransportLeg(mode="公共交通", from_place="A", to_place="B", cost=5, cost_basis="per_person")],
    )
    draft = RequirementInput(travelers=3)

    lines, *_ = _compose_budget(draft, [plan_day], 0, "")

    local = next(line for line in lines if line.key == "local_transport")
    assert local.amount == 15


class _Client:
    def __init__(self, get):
        self._request = get

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, *args, **kwargs):
        return self._request(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self._request(*args, **kwargs)


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("failure", request=httpx.Request("GET", "https://example.test"), response=httpx.Response(self.status_code))
