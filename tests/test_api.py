from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _complete_request(trip_start: date) -> dict:
    return {
        "origin": "上海",
        "destination": "成都",
        "start_date": trip_start.isoformat(),
        "end_date": (trip_start + timedelta(days=2)).isoformat(),
        "travelers": 2,
        "budget": {"amount": 6000, "basis": "total"},
        "interests": ["美食", "历史"],
        "pace": "适中",
        "local_transport": "公共交通",
        "lodging": "舒适型",
        "demo_weather": "sunny",
    }


def test_health_and_meta(client: TestClient, trip_start: date):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    meta = client.get("/api/meta")
    assert meta.status_code == 200
    body = meta.json()
    assert "成都" in body["supported_cities"]
    assert body["destination_cities"] == body["supported_cities"]
    assert "长沙" not in body["destination_cities"]
    assert body["demo_mode"] is True
    assert body["data_policy"]


def test_province_requires_city_then_clarify_and_generate(client: TestClient, trip_start: date):
    analyze = client.post(
        "/api/plan/analyze",
        json={
            "raw_text": f"从北京去四川玩，{(trip_start + timedelta(days=1)).month}月"
            f"{(trip_start + timedelta(days=1)).day}日到{(trip_start + timedelta(days=3)).day}日，"
            "2个人，总预算6000元",
        },
    )
    assert analyze.status_code == 200
    first = analyze.json()
    assert first["status"] == "need_clarification"
    assert first["questions"][0]["id"] == "destination_city"

    session_id = first["session_id"]
    clarify = client.post(
        "/api/plan/clarify",
        json={"session_id": session_id, "answers": {"destination_city": "成都"}},
    )
    assert clarify.status_code == 200
    second = clarify.json()
    assert second["draft"]["destination"] == "成都"

    # 仍未给出具体日期时保持澄清状态，不能直接生成
    if second["status"] != "ready":
        response = client.post("/api/plan/clarify", json={
            "session_id": session_id,
            "answers": {
                "destination_city": "成都",
                "date_range": f"{trip_start.isoformat()}|{(trip_start + timedelta(days=2)).isoformat()}",
            },
        })
        assert response.status_code == 200
        second = response.json()

    assert second["status"] == "ready"

    generate = client.post("/api/plan/generate", json={"session_id": session_id})
    assert generate.status_code == 200
    itinerary = generate.json()
    assert itinerary["destination"] == "成都"
    assert itinerary["days_count"] == 3
    trip_id = itinerary["trip_id"]

    detail = client.get(f"/api/trips/{trip_id}")
    assert detail.status_code == 200
    assert detail.json()["trip_id"] == trip_id

    trips = client.get("/api/trips")
    assert trips.status_code == 200
    assert any(item["trip_id"] == trip_id for item in trips.json()["items"])


def test_generate_rejects_unready_session(client: TestClient):
    analyze = client.post("/api/plan/analyze", json={"raw_text": "我想出去放松一下"})
    session_id = analyze.json()["session_id"]
    response = client.post("/api/plan/generate", json={"session_id": session_id})
    assert response.status_code == 409


def test_export_formats(client: TestClient, trip_start: date):
    analyze = client.post("/api/plan/analyze", json=_complete_request(trip_start))
    assert analyze.json()["status"] == "ready"
    session_id = analyze.json()["session_id"]
    itinerary = client.post("/api/plan/generate", json={"session_id": session_id}).json()
    trip_id = itinerary["trip_id"]

    expectations = {
        "docx": ("PK", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "doc": ("{\\rtf1", "application/msword"),
        "pdf": ("%PDF", "application/pdf"),
    }
    for fmt, (prefix, media_type) in expectations.items():
        response = client.get(f"/api/trips/{trip_id}/export", params={"format": fmt})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(media_type)
        assert response.content.startswith(prefix.encode("latin-1"))

    markdown = client.get(f"/api/trips/{trip_id}/export", params={"format": "md"})
    assert markdown.status_code == 200
    assert "成都" in markdown.text


def test_export_missing_trip_returns_404(client: TestClient):
    response = client.get("/api/trips/not-exists/export", params={"format": "pdf"})
    assert response.status_code == 404


def test_sse_stream_emits_all_agents_in_order(client: TestClient, trip_start: date):
    analyze = client.post("/api/plan/analyze", json=_complete_request(trip_start))
    session_id = analyze.json()["session_id"]

    events: list[str] = []
    result_payload: dict | None = None
    with client.stream("POST", "/api/plan/generate/stream", json={"session_id": session_id}) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
            elif line.startswith("data:") and events and events[-1] == "result":
                result_payload = json.loads(line.split(":", 1)[1].strip())

    starts = [event for event in events if event == "agent_start"]
    assert len(starts) == 6
    assert events.count("agent_done") == 6
    assert events[-1] == "result"
    assert result_payload is not None
    assert result_payload["destination"] == "成都"


def test_meta_expands_destinations_when_amap_is_configured(client: TestClient, monkeypatch):
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")

    body = client.get("/api/meta").json()

    assert "长沙" in body["destination_cities"]
    assert len(body["destination_cities"]) > len(body["supported_cities"])


def test_meta_declares_demo_scope(client: TestClient):
    body = client.get("/api/meta").json()
    providers = body["providers"]
    assert set(providers) == {"llm", "amap", "qweather"}
    if not any(providers.values()):
        assert body["demo_mode"] is True
