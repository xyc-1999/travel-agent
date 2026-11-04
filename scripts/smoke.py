"""对本地运行的 API 做端到端冒烟验证，并导出三种格式到 artifacts/。

用法：
    .venv\\Scripts\\python.exe scripts\\smoke.py --base http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--demo-weather", default="rainy")
    args = parser.parse_args()

    start = date.today() + timedelta(days=15)
    payload = {
        "origin": "上海",
        "destination": "成都",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=2)).isoformat(),
        "travelers": 2,
        "budget": {"amount": 6000, "basis": "total"},
        "interests": ["美食", "历史"],
        "pace": "适中",
        "local_transport": "公共交通",
        "lodging": "舒适型",
        "must_visit": ["宽窄巷子"],
        "demo_weather": args.demo_weather,
    }

    with httpx.Client(base_url=args.base, timeout=60) as client:
        health = client.get("/api/health")
        print("health:", health.status_code, health.json())

        analysis = client.post("/api/plan/analyze", json=payload)
        analysis.raise_for_status()
        body = analysis.json()
        print("analyze:", body["status"], body["session_id"])
        assert body["status"] == "ready", body["questions"]

        events: list[str] = []
        began = time.perf_counter()
        with client.stream("POST", "/api/plan/generate/stream", json={"session_id": body["session_id"]}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
        elapsed = time.perf_counter() - began
        print("sse events:", " -> ".join(events), f"({elapsed:.2f}s)")

        trip = client.get(f"/api/trips").json()["items"][0]["trip_id"]
        detail = client.get(f"/api/trips/{trip}").json()
        print("trip:", trip, detail["destination"], detail["days_count"], "天", detail["budget"]["total"], "元")
        print("demo_mode:", detail["demo_mode"], "|", detail["data_mode_label"])
        print("adaptations:", [day["weather_adaptation"] for day in detail["days"] if day["weather_adaptation"]])

        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        for fmt in ("docx", "doc", "pdf", "md"):
            response = client.get(f"/api/trips/{trip}/export", params={"format": fmt})
            response.raise_for_status()
            path = ARTIFACTS / f"{detail['destination']}-{detail['days_count']}天-行程.{fmt}"
            path.write_bytes(response.content)
            print("export:", path.name, len(response.content), "bytes")

    summary = {
        "events": events,
        "trip_id": trip,
        "demo_mode": detail["demo_mode"],
        "budget_status": detail["budget"]["budget_status"],
    }
    (ARTIFACTS / "smoke-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
