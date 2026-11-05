from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.agents.requirement import analyze_requirement, apply_answers
from app.export import build_doc_rtf, build_docx, build_markdown, build_pdf
from app.models import (
    AnalyzeResponse,
    ClarifyRequest,
    GenerateRequest,
    Itinerary,
    RequirementInput,
    TripListResponse,
)
from app.orchestrator import PipelineError, generate_itinerary, iter_generation
from app.storage import get_session, get_trip, init_db, list_trips, save_session
from app.data_loader import cities_data, supported_cities


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="行迹 · AI 多智能体旅行助手", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "demo_mode": config.DEMO_MODE,
        "providers": {
            "llm": bool(config.LLM_API_KEY),
            "amap": bool(config.AMAP_API_KEY),
            "qweather": bool(config.QWEATHER_API_KEY),
        },
    }


@app.get("/api/meta")
def meta() -> dict:
    city_index = list(cities_data()["cities"].keys())
    return {
        "demo_mode": config.DEMO_MODE,
        "supported_cities": supported_cities(),
        # 配置高德后可用城市扩展到城市索引；否则仅示例库覆盖的 6 座城市
        "destination_cities": city_index if config.AMAP_API_KEY else supported_cities(),
        "city_index": city_index,
        "provinces": cities_data()["provinces"],
        "providers": {
            "llm": bool(config.LLM_API_KEY),
            "amap": bool(config.AMAP_API_KEY),
            "qweather": bool(config.QWEATHER_API_KEY),
        },
        "scope": "第一版：中国境内、单城市、单次旅行规划。",
        "data_policy": (
            "未配置真实 Key 时使用内置示例地点库、示例攻略与历史气候参考；"
            "所有示例数据均标注来源，不冒充实时查询结果。"
        ),
    }


@app.post("/api/plan/analyze", response_model=AnalyzeResponse)
def analyze(payload: RequirementInput) -> AnalyzeResponse:
    session_id = uuid.uuid4().hex[:12]
    analysis = analyze_requirement(payload, session_id=session_id)
    save_session(session_id, payload, analysis)
    return analysis


@app.post("/api/plan/clarify", response_model=AnalyzeResponse)
def clarify(request: ClarifyRequest) -> AnalyzeResponse:
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    payload, _ = session
    payload = apply_answers(payload, request.answers)
    analysis = analyze_requirement(payload, session_id=request.session_id, answers=request.answers)
    save_session(request.session_id, payload, analysis)
    return analysis


@app.post("/api/plan/generate")
def generate(request: GenerateRequest) -> Itinerary:
    try:
        return generate_itinerary(request.session_id)
    except PipelineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/plan/generate/stream")
async def generate_stream(request: GenerateRequest) -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def worker() -> None:
        try:
            for event in iter_generation(request.session_id):
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"event": "error", "data": {"message": f"生成失败：{type(exc).__name__}: {exc}"}},
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    import threading

    threading.Thread(target=worker, daemon=True).start()

    async def event_stream():
        while True:
            event = await queue.get()
            if event is None:
                break
            payload = json.dumps(event["data"], ensure_ascii=False, default=str)
            yield f"event: {event['event']}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/trips", response_model=TripListResponse)
def trips(limit: int = Query(default=12, ge=1, le=50)) -> TripListResponse:
    return list_trips(limit=limit)


@app.get("/api/trips/{trip_id}")
def trip_detail(trip_id: str) -> Itinerary:
    itinerary = get_trip(trip_id)
    if not itinerary:
        raise HTTPException(status_code=404, detail="行程不存在")
    return itinerary


@app.get("/api/trips/{trip_id}/export")
def export_trip(trip_id: str, format: str = Query(default="docx", pattern="^(docx|doc|pdf|md)$")):
    itinerary = get_trip(trip_id)
    if not itinerary:
        raise HTTPException(status_code=404, detail="行程不存在")

    if format == "docx":
        content = build_docx(itinerary)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif format == "doc":
        content = build_doc_rtf(itinerary)
        media_type = "application/msword"
    elif format == "pdf":
        content = build_pdf(itinerary)
        media_type = "application/pdf"
    else:
        content = build_markdown(itinerary).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"

    filename = f"{itinerary.destination}-{itinerary.days_count}天-行程.{format}"
    disposition = f"attachment; filename=\"itinerary.{format}\"; filename*=UTF-8''{quote(filename)}"
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": disposition})


static_dir = Path(config.STATIC_DIR)
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
