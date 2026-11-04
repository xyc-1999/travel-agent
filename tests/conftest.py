from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试使用独立的 D 盘数据库，避免污染演示数据
os.environ["DATABASE_PATH"] = "data/test_app.db"

import pytest  # noqa: E402

from app import config  # noqa: E402
from app.models import BudgetSpec, RequirementInput  # noqa: E402
from app.storage import init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if config.DATABASE_PATH.exists():
        config.DATABASE_PATH.unlink()
    init_db()
    yield
    if config.DATABASE_PATH.exists():
        config.DATABASE_PATH.unlink()


@pytest.fixture
def trip_start() -> date:
    return date.today() + timedelta(days=20)


@pytest.fixture
def complete_payload(trip_start: date) -> RequirementInput:
    return RequirementInput(
        origin="上海",
        destination="成都",
        start_date=trip_start,
        end_date=trip_start + timedelta(days=2),
        travelers=2,
        budget=BudgetSpec(amount=6000, basis="total"),
        interests=["美食", "历史"],
        intercity_mode="高铁",
        local_transport="公共交通",
        lodging="舒适型",
        pace="适中",
        must_visit=["宽窄巷子"],
        demo_weather="sunny",
    )


@pytest.fixture
def sample_itinerary(complete_payload: RequirementInput):
    from app.agents.requirement import analyze_requirement
    from app.orchestrator import generate_itinerary
    from app.storage import save_session

    analysis = analyze_requirement(complete_payload, session_id=uuid.uuid4().hex[:12])
    assert analysis.status == "ready"
    save_session(analysis.session_id, complete_payload, analysis)
    return generate_itinerary(analysis.session_id)
