from __future__ import annotations

from app import config
from app.data_loader import city_coordinates
from app.services.weather import _qweather_weather_url, _weather_location


def test_qweather_url_uses_custom_api_host(monkeypatch):
    monkeypatch.setattr(config, "QWEATHER_API_HOST", "https://abc123.qweatherapi.com")

    assert _qweather_weather_url() == "https://abc123.qweatherapi.com/v7/weather/3d"


def test_qweather_url_adds_scheme_and_strips_slash(monkeypatch):
    monkeypatch.setattr(config, "QWEATHER_API_HOST", "abc123.qweatherapi.com/")

    assert _qweather_weather_url() == "https://abc123.qweatherapi.com/v7/weather/3d"


def test_qweather_url_falls_back_to_legacy_host(monkeypatch):
    monkeypatch.setattr(config, "QWEATHER_API_HOST", "")

    assert _qweather_weather_url() == "https://devapi.qweather.com/v7/weather/3d"


def test_weather_location_uses_local_city_coordinates():
    lat, lng = city_coordinates("成都") or (0, 0)

    assert _weather_location("成都") == f"{lng:.2f},{lat:.2f}"


def test_weather_location_returns_none_for_unknown_city():
    assert _weather_location("某未知城市") is None
