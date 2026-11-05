from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class BudgetSpec(BaseModel):
    amount: float | None = Field(default=None, ge=0)
    basis: Literal["total", "per_person"] | None = None
    skip: bool = False  # 用户明确选择“不设预算，只做估算”


class RequirementInput(BaseModel):
    """用户通过表单或自然语言提交的原始需求。"""

    raw_text: str | None = None
    origin: str | None = None
    destination: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=30)
    travelers: int | None = Field(default=None, ge=1, le=20)
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    interests: list[str] = Field(default_factory=list)
    intercity_mode: Literal["高铁", "飞机", "自驾", "不限"] | None = None
    local_transport: Literal["公共交通", "打车", "步行", "包车"] | None = None
    lodging: Literal["经济型", "舒适型", "豪华型", "民宿"] | None = None
    pace: Literal["轻松", "适中", "紧凑"] | None = None
    must_visit: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    notes: str | None = None
    demo_weather: Literal["auto", "rainy", "sunny"] = "auto"


class RequirementQuestion(BaseModel):
    id: str
    field: str
    prompt: str
    type: Literal["text", "number", "date", "date_range", "select", "budget_basis"]
    options: list[dict[str, str]] = Field(default_factory=list)
    hint: str | None = None


class Conflict(BaseModel):
    code: str
    message: str
    fields: list[str] = Field(default_factory=list)


class RequirementAnalysis(BaseModel):
    session_id: str
    status: Literal["need_clarification", "ready"]
    questions: list[RequirementQuestion] = Field(default_factory=list)
    draft: RequirementInput
    conflicts: list[Conflict] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    summary: str


class AnalyzeResponse(RequirementAnalysis):
    pass


class ClarifyRequest(BaseModel):
    session_id: str
    answers: dict[str, str] = Field(default_factory=dict)


class GenerateRequest(BaseModel):
    session_id: str


class Place(BaseModel):
    id: str
    city: str
    name: str
    category: Literal["attraction", "food", "hotel"]
    tags: list[str] = Field(default_factory=list)
    indoor: bool = False
    lat: float
    lng: float
    address: str = ""
    open_time: str = "09:00"
    close_time: str = "17:00"
    closed_weeks: list[int] = Field(default_factory=list)
    duration_minutes: int = 90
    ticket_price: float = 0.0
    avg_price: float = 0.0
    price_per_night: float = 0.0
    level: str = ""
    cuisine: list[str] = Field(default_factory=list)
    description: str = ""
    source: str = "demo_catalog"


class PlaceCheck(BaseModel):
    place: Place
    level: Literal["catalog_checked", "live_poi"]
    checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    places: list[PlaceCheck] = Field(default_factory=list)
    must_visit_ids: list[str] = Field(default_factory=list)
    unresolved_must_visit: list[str] = Field(default_factory=list)
    unavailable_must_visit: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_mode: str = "demo_catalog"


class WeatherDay(BaseModel):
    date: date
    high: int
    low: int
    rain_probability: int = 0
    condition: str = "气候参考"
    source: str = "climate_reference"
    is_forecast: bool = False
    outdoor_ok: bool = True
    note: str = ""
    advice: str = ""


class ResearchSnippet(BaseModel):
    source: str
    title: str
    text: str
    score: float = 0.0


class ResearchResult(BaseModel):
    snippets: list[ResearchSnippet] = Field(default_factory=list)
    source_mode: str = "local_sample_guide"
    note: str = ""


class TransportLeg(BaseModel):
    mode: str
    from_place: str
    to_place: str
    distance_km: float = 0.0
    minutes: int = 0
    cost: float = 0.0
    cost_basis: Literal["per_group", "per_person"] = "per_group"


class ScheduleItem(BaseModel):
    kind: Literal["attraction", "meal", "hotel", "transport", "buffer"]
    name: str
    place_id: str | None = None
    start_time: str = ""
    end_time: str = ""
    duration_minutes: int = 0
    cost: float = 0.0
    cost_basis: Literal["per_person", "per_room", "per_group"] = "per_person"
    note: str = ""
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    indoor: bool = False
    verification_level: str | None = None
    must_visit: bool = False
    tags: list[str] = Field(default_factory=list)


class DayPlan(BaseModel):
    day_index: int
    date: date
    theme: str
    weather: WeatherDay
    items: list[ScheduleItem] = Field(default_factory=list)
    legs: list[TransportLeg] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    weather_adaptation: list[str] = Field(default_factory=list)


class BudgetLine(BaseModel):
    key: str
    label: str
    amount: float
    detail: str = ""


class BudgetPlan(BaseModel):
    currency: str = "CNY"
    lines: list[BudgetLine] = Field(default_factory=list)
    total: float = 0.0
    original_total: float = 0.0
    per_person: float = 0.0
    limit_total: float | None = None
    budget_status: Literal["ok", "over_budget", "no_budget"] = "no_budget"
    remaining: float | None = None
    over_budget_amount: float = 0.0
    tolerance_amount: float = 0.0
    within_tolerance: bool = True
    adjustments: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class AgentTrace(BaseModel):
    agent: str
    label: str
    status: Literal["done", "warning", "error"] = "done"
    summary: str = ""
    details: list[str] = Field(default_factory=list)
    duration_ms: int = 0


class SourceRef(BaseModel):
    type: str
    title: str
    detail: str
    level: str = "demo"
    verified: bool = False


class Itinerary(BaseModel):
    trip_id: str
    created_at: datetime
    destination: str
    origin: str | None = None
    start_date: date
    end_date: date
    days_count: int
    travelers: int
    summary: str
    days: list[DayPlan] = Field(default_factory=list)
    budget: BudgetPlan
    tips: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    verification_summary: list[str] = Field(default_factory=list)
    demo_mode: bool = True
    data_mode_label: str = "演示模式"
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    agent_trace: list[AgentTrace] = Field(default_factory=list)
    request_snapshot: RequirementInput


class TripSummaryItem(BaseModel):
    trip_id: str
    destination: str
    start_date: date
    end_date: date
    days_count: int
    travelers: int
    estimated_total: float
    created_at: datetime


class TripListResponse(BaseModel):
    total: int
    items: list[TripSummaryItem] = Field(default_factory=list)
