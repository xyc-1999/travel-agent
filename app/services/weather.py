from __future__ import annotations

from datetime import date, timedelta

import httpx

from app import config
from app.data_loader import city_coordinates, climate_for, normalize_city_name
from app.models import WeatherDay


def _climate_day(city: str, day: date) -> WeatherDay:
    data = climate_for(city)
    if not data:
        lat, _ = city_coordinates(city) or (30.0, 120.0)
        month = day.month
        seasonal = 12 + (lat - 25) * -0.55 + (month - 6) * (2.0 if lat > 33 else 1.2)
        high = round(seasonal + 5)
        low = round(seasonal - 5)
        return WeatherDay(
            date=day,
            high=high,
            low=low,
            rain_probability=35,
            condition="粗略气候估算",
            source="climate_estimate",
            is_forecast=False,
            outdoor_ok=True,
            note="缺少该城市的历史气候表，温度与降水为粗略估算（演示）。",
            advice="按常规户外行程安排，出行前请自行查看实时天气。",
        )

    month_index = day.month - 1
    high = int(data["high"][month_index])
    low = int(data["low"][month_index])
    rain = int(data["rain"][month_index])
    outdoor_ok = rain < 60 and high < 35
    return WeatherDay(
        date=day,
        high=high,
        low=low,
        rain_probability=rain,
        condition="历史同期气候",
        source="climate_reference",
        is_forecast=False,
        outdoor_ok=outdoor_ok,
        note="历史气候参考，不是天气预报。",
        advice=(
            "同期降水偏多，优先安排室内景点并带雨具。"
            if not outdoor_ok
            else "适合户外活动，仍建议出行前查看实时天气。"
        ),
    )


def _qweather_forecast(city: str, start: date, end: date) -> dict[date, WeatherDay]:
    if not config.QWEATHER_API_KEY or (end - start).days > 6:
        return {}
    host = config.QWEATHER_API_HOST or "https://devapi.qweather.com"
    geo_host = "https://geoapi.qweather.com"
    headers = {"Accept": "application/json"}
    try:
        with httpx.Client(timeout=config.QWEATHER_TIMEOUT_SECONDS, headers=headers) as client:
            geo = client.get(
                f"{geo_host}/v2/city/lookup",
                params={"location": normalize_city_name(city), "key": config.QWEATHER_API_KEY},
            )
            if geo.status_code != 200:
                return {}
            locations = geo.json().get("location") or []
            if not locations:
                return {}
            location_id = locations[0]["id"]
            response = client.get(
                f"{host}/v7/weather/3d",
                params={"location": location_id, "key": config.QWEATHER_API_KEY},
            )
            if response.status_code != 200:
                return {}
            daily = response.json().get("daily") or []
    except (httpx.HTTPError, KeyError, ValueError):
        return {}

    result: dict[date, WeatherDay] = {}
    for item in daily:
        try:
            day = date.fromisoformat(item["fxDate"])
        except (KeyError, ValueError):
            continue
        if not (start <= day <= end):
            continue
        high = int(item.get("tempMax", 25))
        low = int(item.get("tempMin", 15))
        text = str(item.get("textDay", "未知"))
        rain = 80 if any(word in text for word in ("雨", "雪", "雷")) else 20
        outdoor_ok = not any(word in text for word in ("雨", "雪", "雷")) and high < 35
        result[day] = WeatherDay(
            date=day,
            high=high,
            low=low,
            rain_probability=rain,
            condition=text,
            source="qweather_forecast",
            is_forecast=True,
            outdoor_ok=outdoor_ok,
            note="和风天气 3 天预报。",
            advice=(
                "有降水或高温，优先室内景点并准备雨具。"
                if not outdoor_ok
                else "天气条件允许户外活动，仍建议出发前复核。"
            ),
        )
    return result


def build_weather(city: str, start: date, end: date, demo_weather: str = "auto") -> tuple[list[WeatherDay], str]:
    days: list[WeatherDay] = []
    provider = "climate_reference"

    forecast_map: dict[date, WeatherDay] = {}
    if demo_weather == "auto":
        forecast_map = _qweather_forecast(city, start, end)
        if forecast_map:
            provider = "qweather_forecast"

    current = start
    while current <= end:
        if demo_weather == "rainy":
            days.append(
                WeatherDay(
                    date=current,
                    high=26,
                    low=19,
                    rain_probability=85,
                    condition="雨（演示）",
                    source="demo_forced",
                    is_forecast=False,
                    outdoor_ok=False,
                    note="手动触发的雨天演示，用于验证天气适配逻辑，并非真实天气。",
                    advice="优先安排室内景点，减少长时间户外活动。",
                )
            )
        elif demo_weather == "sunny":
            days.append(
                WeatherDay(
                    date=current,
                    high=28,
                    low=18,
                    rain_probability=5,
                    condition="晴（演示）",
                    source="demo_forced",
                    is_forecast=False,
                    outdoor_ok=True,
                    note="手动触发的晴天演示，并非真实天气。",
                    advice="适合户外活动。",
                )
            )
        elif current in forecast_map:
            days.append(forecast_map[current])
        else:
            days.append(_climate_day(city, current))
        current = current + timedelta(days=1)

    return days, provider
