from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "app" / "data"
STATIC_DIR = BASE_DIR / "static"
ARTIFACTS_DIR = BASE_DIR / "artifacts"


def _load_dotenv(path: Path) -> None:
    """读取本地 .env，避免额外引入第三方依赖。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


LLM_API_KEY = _env("LLM_API_KEY")
LLM_BASE_URL = _env("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
LLM_MODEL = _env("LLM_MODEL", "qwen-plus")
LLM_TIMEOUT_SECONDS = float(_env("LLM_TIMEOUT_SECONDS", "60"))

AMAP_API_KEY = _env("AMAP_API_KEY")
AMAP_TIMEOUT_SECONDS = float(_env("AMAP_TIMEOUT_SECONDS", "15"))

QWEATHER_API_KEY = _env("QWEATHER_API_KEY")
QWEATHER_API_HOST = _env("QWEATHER_API_HOST").rstrip("/")
QWEATHER_TIMEOUT_SECONDS = float(_env("QWEATHER_TIMEOUT_SECONDS", "15"))

DATABASE_PATH = BASE_DIR / _env("DATABASE_PATH", "data/app.db")
DEMO_MODE = not bool(LLM_API_KEY or AMAP_API_KEY or QWEATHER_API_KEY)

SUPPORTED_CITIES = ("北京", "上海", "成都", "杭州", "西安", "大理")
CORS_ORIGINS = tuple(origin.strip() for origin in _env("CORS_ORIGINS", "http://127.0.0.1:8765,http://localhost:8765").split(",") if origin.strip())
