from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app import config
from app.models import (
    Itinerary,
    RequirementAnalysis,
    RequirementInput,
    TripListResponse,
    TripSummaryItem,
)


def _connect() -> sqlite3.Connection:
    database_path: Path = config.DATABASE_PATH
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _session():
    connection = _connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with _session() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                analysis TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY,
                itinerary TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def save_session(session_id: str, payload: RequirementInput, analysis: RequirementAnalysis) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with _session() as connection:
        connection.execute(
            """
            INSERT INTO sessions (id, payload, analysis, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload,
                analysis = excluded.analysis, updated_at = excluded.updated_at
            """,
            (
                session_id,
                payload.model_dump_json(),
                analysis.model_dump_json(),
                now,
                now,
            ),
        )


def get_session(session_id: str) -> tuple[RequirementInput, RequirementAnalysis] | None:
    with _session() as connection:
        row = connection.execute("SELECT payload, analysis FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    return (
        RequirementInput.model_validate_json(row["payload"]),
        RequirementAnalysis.model_validate_json(row["analysis"]),
    )


def save_trip(itinerary: Itinerary) -> None:
    with _session() as connection:
        connection.execute(
            """
            INSERT INTO trips (id, itinerary, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET itinerary = excluded.itinerary
            """,
            (itinerary.trip_id, itinerary.model_dump_json(), itinerary.created_at.isoformat()),
        )


def get_trip(trip_id: str) -> Itinerary | None:
    with _session() as connection:
        row = connection.execute("SELECT itinerary FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not row:
        return None
    return Itinerary.model_validate_json(row["itinerary"])


def list_trips(limit: int = 20) -> TripListResponse:
    with _session() as connection:
        total = connection.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        rows = connection.execute(
            "SELECT itinerary FROM trips ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    items: list[TripSummaryItem] = []
    for row in rows:
        itinerary = Itinerary.model_validate_json(row["itinerary"])
        items.append(
            TripSummaryItem(
                trip_id=itinerary.trip_id,
                destination=itinerary.destination,
                start_date=itinerary.start_date,
                end_date=itinerary.end_date,
                days_count=itinerary.days_count,
                travelers=itinerary.travelers,
                estimated_total=itinerary.budget.total,
                created_at=itinerary.created_at,
            )
        )
    return TripListResponse(total=total, items=items)
