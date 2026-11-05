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
