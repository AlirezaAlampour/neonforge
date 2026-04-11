"""
API Gateway — routes requests, enforces job queue, memory gating.

Design:
  - All client requests enter through this gateway.
  - GPU jobs are serialized: heavy=1, medium=1 concurrency.
  - A Redis-backed queue tracks job state for observability.
  - UMA memory checks gate new GPU jobs (default hard limit: 80%).
  - Per-tier memory reservations prevent overcommit.
  - Orchestration (start/stop lazy containers) is delegated to
    the supervisor sidecar — this gateway has NO Docker socket access.
  - Persistent generation history + preset profiles are stored in SQLite.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
import difflib
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import DateTime, String, Text, UniqueConstraint, create_engine, desc, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# ---------------------------------------------------------------------------
# Env parsing helpers
# ---------------------------------------------------------------------------

def _parse_path_list_env(value: Optional[str], defaults: list[str]) -> list[Path]:
    parts = re.split(r"[\n,;]+", value) if value and value.strip() else defaults
    roots: list[Path] = []
    seen: set[str] = set()
    for part in parts:
        normalized = part.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        roots.append(Path(normalized))
    return roots


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_str_list_env(value: Optional[str], defaults: list[str]) -> list[str]:
    parts = re.split(r"[\n,;]+", value) if value and value.strip() else defaults
    items: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = part.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items


def _parse_int_list_env(value: Optional[str], defaults: list[int]) -> list[int]:
    items: list[int] = []
    for raw in _parse_str_list_env(value, [str(item) for item in defaults]):
        try:
            items.append(int(raw))
        except ValueError:
            continue
    return items


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://supervisor:8000")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://comfyui:8188")

OUTPUTS_ROOT = Path(os.getenv("OUTPUTS_ROOT", "/outputs"))
HISTORY_DB_PATH = Path(os.getenv("HISTORY_DB_PATH", str(OUTPUTS_ROOT / "neonforge_history.sqlite3")))
ASSETS_ROOT = Path(os.getenv("ASSETS_ROOT", "/app/data/assets"))
GATEWAY_ROOT = Path(__file__).resolve().parent
VOICE_ASSETS_DIR = Path(os.getenv("VOICE_ASSETS_DIR", str(ASSETS_ROOT / "voices")))
LORA_ASSETS_DIR = Path(os.getenv("LORA_ASSETS_DIR", str(ASSETS_ROOT / "loras")))
COMFYUI_TEMPLATE_DIR = Path(
    os.getenv("COMFYUI_TEMPLATE_DIR", str(GATEWAY_ROOT / "templates" / "comfyui"))
)
COMFYUI_UPLOADS_DIR = Path(
    os.getenv("COMFYUI_UPLOADS_DIR", str(ASSETS_ROOT / "comfyui" / "uploads"))
)
COMFYUI_INPUT_DIR = Path(
    os.getenv("COMFYUI_INPUT_DIR", str(OUTPUTS_ROOT / "comfyui" / "input"))
)
COMFYUI_MODEL_ROOTS = _parse_path_list_env(
    os.getenv("COMFYUI_MODEL_ROOTS"),
    [
        "/models/comfyui",
        "/opt/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts",
    ],
)
COMFYUI_MAX_IMAGE_MB = int(os.getenv("COMFYUI_MAX_IMAGE_MB", "20"))
COMFYUI_MAX_VIDEO_MB = int(os.getenv("COMFYUI_MAX_VIDEO_MB", "500"))
COMFYUI_POLL_INTERVAL_SEC = float(os.getenv("COMFYUI_POLL_INTERVAL_SEC", "2"))
COMFYUI_JOB_TIMEOUT_SEC = int(os.getenv("COMFYUI_JOB_TIMEOUT_SEC", "14400"))
COMFYUI_DEBUG_DIR = Path(
    os.getenv("COMFYUI_DEBUG_DIR", str(Path(tempfile.gettempdir()) / "neonforge-comfyui-debug"))
)

# UMA memory gating
MEMORY_WARN_PCT = int(os.getenv("MEMORY_WARN_THRESHOLD", "70"))
MEMORY_HARD_PCT = int(os.getenv("MEMORY_HARD_LIMIT", "80"))

# Per-tier memory reservations (GB that must remain free for tier admission)
MEM_RESERVE_HEAVY_GB = float(os.getenv("MEM_RESERVE_HEAVY_GB", "40"))
MEM_RESERVE_MEDIUM_GB = float(os.getenv("MEM_RESERVE_MEDIUM_GB", "10"))
MEM_RESERVE_LIGHT_GB = float(os.getenv("MEM_RESERVE_LIGHT_GB", "2"))

SERVICE_URLS = {
    "whisper": os.getenv("WHISPER_URL", "http://whisper:8000"),
    "f5tts": os.getenv("F5TTS_URL", "http://f5tts:8000"),
    "liveportrait": os.getenv("LIVEPORTRAIT_URL", "http://liveportrait:8000"),
    "lipsync": os.getenv("LIPSYNC_URL", "http://lipsync:8000"),
    "wan21": os.getenv("WAN21_URL", "http://wan21:8000"),
}

if _env_bool("FISH_SPEECH_ENABLED", False):
    SERVICE_URLS["fish_speech"] = os.getenv("FISH_SPEECH_URL", "http://fish_speech:8000")

if _env_bool("PREMIUM_CLONE_TTS_ENABLED", False):
    SERVICE_URLS["premium_clone_tts"] = os.getenv("PREMIUM_CLONE_TTS_URL", "http://premium_clone_tts:8000")

MODEL_NAME_BY_SERVICE = {
    "whisper": "Faster-Whisper",
    "f5tts": "F5-TTS",
    "fish_speech": os.getenv("FISH_SPEECH_DISPLAY_NAME", "Fish Speech"),
    "premium_clone_tts": os.getenv("PREMIUM_CLONE_TTS_DISPLAY_NAME", "Premium Clone TTS"),
    "liveportrait": "LivePortrait",
    "lipsync": os.getenv("LIPSYNC_BACKEND", "video-retalking"),
    "wan21": f"Wan 2.1 {os.getenv('WAN21_MODEL_VARIANT', '1.3B')}",
    "reactor": "ComfyUI/ReActor",
    "comfyui": "ComfyUI Template Workflow",
}

GPU_HEAVY_SERVICES = {"wan21"}
GPU_MEDIUM_SERVICES = {"liveportrait", "lipsync", "f5tts", "fish_speech", "premium_clone_tts", "reactor"}
GPU_LIGHT_SERVICES = {"whisper"}

VOICE_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".m4a",
    ".webm",
}
LORA_EXTENSIONS = {
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
}
COMFYUI_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
}
COMFYUI_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
}
COMFYUI_MODEL_EXTENSIONS = {
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
    ".onnx",
}

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("gateway")


# ---------------------------------------------------------------------------
# Database (SQLAlchemy + SQLite)
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class GenerationHistory(Base):
    __tablename__ = "generation_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    service: Mapped[str] = mapped_column(String(64), index=True)
    model_used: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parameters_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PresetProfile(Base):
    __tablename__ = "preset_profiles"
    __table_args__ = (
        UniqueConstraint("name", "tool", name="uq_preset_name_tool"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    tool: Mapped[str] = mapped_column(String(64), index=True)
    state_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ComfyUIAssetRecord(Base):
    __tablename__ = "comfyui_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    relative_path: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ComfyUIJobRecordDB(Base):
    __tablename__ = "comfyui_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(128), index=True)
    template_name: Mapped[str] = mapped_column(String(255))
    gpu_tier: Mapped[str] = mapped_column(String(16), default="heavy")
    client_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prompt_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    inputs_json: Mapped[str] = mapped_column(Text, default="{}")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifacts_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    history_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    status_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_db_url = f"sqlite:///{HISTORY_DB_PATH}"
db_engine = create_engine(_db_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _sqlite_table_columns(table_name: str) -> set[str]:
    with db_engine.begin() as conn:
        try:
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table_name})").mappings().all()
        except Exception:
            return set()
    return {str(row.get("name")) for row in rows if row.get("name")}


def run_sqlite_schema_migrations():
    required_columns = {
        "comfyui_jobs": {
            "artifacts_json": "TEXT",
        },
    }
    for table_name, columns in required_columns.items():
        existing = _sqlite_table_columns(table_name)
        if not existing:
            continue
        for column_name, column_type in columns.items():
            if column_name in existing:
                continue
            with db_engine.begin() as conn:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                )


# ---------------------------------------------------------------------------
# Memory helpers (UMA-aware)
# ---------------------------------------------------------------------------

def read_meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return info


def get_memory_status() -> dict[str, Any]:
    mi = read_meminfo()
    total_kb = mi.get("MemTotal", 0)
    available_kb = mi.get("MemAvailable", 0)
    swap_total_kb = mi.get("SwapTotal", 0)
    swap_free_kb = mi.get("SwapFree", 0)
    used_kb = total_kb - available_kb
    used_pct = (used_kb / total_kb * 100) if total_kb else 0
    swap_used_kb = swap_total_kb - swap_free_kb
    return {
        "total_gb": round(total_kb / 1048576, 1),
        "available_gb": round(available_kb / 1048576, 1),
        "used_gb": round(used_kb / 1048576, 1),
        "used_pct": round(used_pct, 1),
        "swap_total_gb": round(swap_total_kb / 1048576, 1),
        "swap_free_gb": round(swap_free_kb / 1048576, 1),
        "swap_used_gb": round(swap_used_kb / 1048576, 1),
        "swap_used_pct": round(
            (swap_used_kb / swap_total_kb * 100) if swap_total_kb else 0, 1
        ),
        "thresholds": {
            "warn_pct": MEMORY_WARN_PCT,
            "hard_pct": MEMORY_HARD_PCT,
            "reserve_heavy_gb": MEM_RESERVE_HEAVY_GB,
            "reserve_medium_gb": MEM_RESERVE_MEDIUM_GB,
            "reserve_light_gb": MEM_RESERVE_LIGHT_GB,
        },
    }


def memory_allows_job(tier: str) -> tuple[bool, dict[str, Any], str]:
    """Check if UMA allows a new job for the given tier."""
    status = get_memory_status()
    avail_gb = status["available_gb"]

    if status["used_pct"] >= MEMORY_HARD_PCT:
        return False, status, f"UMA usage {status['used_pct']}% >= hard limit {MEMORY_HARD_PCT}%"

    reserve = {
        "heavy": MEM_RESERVE_HEAVY_GB,
        "medium": MEM_RESERVE_MEDIUM_GB,
        "light": MEM_RESERVE_LIGHT_GB,
    }.get(tier, MEM_RESERVE_MEDIUM_GB)

    if avail_gb < reserve:
        return False, status, (
            f"Available {avail_gb:.1f} GB < {tier} tier reservation {reserve:.1f} GB"
        )

    return True, status, "ok"


# ---------------------------------------------------------------------------
# Filesystem safety helpers
# ---------------------------------------------------------------------------

def _ensure_within_root(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise HTTPException(403, "Access denied")
    return resolved


def resolve_output_path(relative_or_abs_path: str) -> Path:
    candidate = Path(relative_or_abs_path)
    if candidate.is_absolute():
        return _ensure_within_root(candidate, OUTPUTS_ROOT)
    return _ensure_within_root(OUTPUTS_ROOT / candidate, OUTPUTS_ROOT)


def normalize_output_path(relative_or_abs_path: str) -> str:
    resolved = resolve_output_path(relative_or_abs_path)
    return str(resolved.relative_to(OUTPUTS_ROOT.resolve()))


def resolve_asset_path(input_path: str, asset_root: Path) -> Path:
    candidate = Path(input_path)
    if not candidate.is_absolute():
        candidate = asset_root / candidate
    resolved = _ensure_within_root(candidate, asset_root)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(400, f"Asset file not found: {input_path}")
    return resolved


# ---------------------------------------------------------------------------
# History + Preset helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json_loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


COMFYUI_INTERNAL_DEBUG_PARAM = "_neonforge_debug_dump"
WAN_CHARACTER_SWAP_POSITIVE_POINTS_PARAM = "subject_points_json"
WAN_CHARACTER_SWAP_NEGATIVE_POINTS_PARAM = "negative_points_json"
WAN_CHARACTER_SWAP_DEBUG_OUTPUTS = [
    {
        "id": "preview_collage",
        "label": "Preview Collage",
        "node_id": "172",
        "media_keys": ["gifs", "videos", "images", "files"],
        "filename_prefix": "neonforge/{job_id}/debug-preview-collage",
    },
    {
        "id": "pose_video",
        "label": "Pose Preview",
        "node_id": "173",
        "media_keys": ["gifs", "videos", "images", "files"],
        "filename_prefix": "neonforge/{job_id}/debug-pose",
    },
    {
        "id": "raw_segmentation_video",
        "label": "Raw Segmentation Overlay",
        "node_id": "175",
        "media_keys": ["gifs", "videos", "images", "files"],
        "filename_prefix": "neonforge/{job_id}/debug-raw-segmentation",
    },
    {
        "id": "decoded_frames_video",
        "label": "Decoded Frames",
        "node_id": "176",
        "media_keys": ["gifs", "videos", "images", "files"],
        "filename_prefix": "neonforge/{job_id}/debug-decoded",
    },
    {
        "id": "face_crop_video",
        "label": "Face Crop Preview",
        "node_id": "112",
        "media_keys": ["gifs", "videos", "images", "files"],
        "filename_prefix": "neonforge/{job_id}/debug-face-crop",
    },
    {
        "id": "blockified_mask_video",
        "label": "Blockified Mask Overlay",
        "node_id": "75",
        "media_keys": ["gifs", "videos", "images", "files"],
        "filename_prefix": "neonforge/{job_id}/debug-blockified-mask",
    },
]
WAN_CHARACTER_SWAP_MANUAL_POSITIVE_POINTS = [
    {"x": 575.8604020500962, "y": 461.00299638143633},
    {"x": 589.0269647654002, "y": 105.50580306822965},
]
WAN_CHARACTER_SWAP_MANUAL_NEGATIVE_POINTS = [{"x": 0.0, "y": 0.0}]
WAN_CHARACTER_SWAP_DIFF_NODE_IDS = ("27", "30", "62", "63", "96", "104", "107", "108", "120")


def _comfyui_error_detail(error: str, **context: Any) -> dict[str, Any]:
    detail = {"error": error}
    detail.update({key: value for key, value in context.items() if value is not None})
    return detail


def _http_error_text(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        message = detail.get("error") or detail.get("message")
        if isinstance(message, str) and message.strip():
            return message
        try:
            return json.dumps(detail, ensure_ascii=False)
        except TypeError:
            return str(detail)
    return str(detail)


def _parse_points_json(value: Any, *, field_name: str) -> list[dict[str, float]]:
    raw_value = value
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                400,
                _comfyui_error_detail(
                    f"Invalid JSON for {field_name}.",
                    field_name=field_name,
                ),
            ) from exc
    if not isinstance(raw_value, list):
        raise HTTPException(
            400,
            _comfyui_error_detail(
                f"{field_name} must be a JSON array of point objects.",
                field_name=field_name,
            ),
        )

    points: list[dict[str, float]] = []
    for index, item in enumerate(raw_value):
        if not isinstance(item, dict):
            raise HTTPException(
                400,
                _comfyui_error_detail(
                    f"{field_name}[{index}] must be an object with x/y coordinates.",
                    field_name=field_name,
                    point_index=index,
                ),
            )
        try:
            x = float(item["x"])
            y = float(item["y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                400,
                _comfyui_error_detail(
                    f"{field_name}[{index}] must include numeric x and y values.",
                    field_name=field_name,
                    point_index=index,
                ),
            ) from exc
        points.append({"x": x, "y": y})
    return points


def _load_points_editor_points(node_inputs: dict[str, Any], key: str, *, field_name: str) -> list[dict[str, float]]:
    raw = node_inputs.get(key)
    if raw is None:
        return []
    return _parse_points_json(raw, field_name=field_name)


def _set_points_editor_inputs(
    node_inputs: dict[str, Any],
    *,
    positive_points: list[dict[str, float]],
    negative_points: list[dict[str, float]],
):
    node_inputs["coordinates"] = json.dumps(positive_points, ensure_ascii=False, separators=(",", ":"))
    node_inputs["neg_coordinates"] = json.dumps(negative_points, ensure_ascii=False, separators=(",", ":"))
    node_inputs["points_store"] = json.dumps(
        {
            "positive": positive_points,
            "negative": negative_points,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _extract_history_media_items(
    history_entry: dict[str, Any],
    *,
    node_id: str,
    media_keys: list[str],
) -> list[dict[str, Any]]:
    outputs = history_entry.get("outputs") or {}
    payload = outputs.get(str(node_id))
    if not isinstance(payload, dict):
        return []

    items: list[dict[str, Any]] = []
    for media_key in media_keys:
        media_items = payload.get(media_key)
        if not isinstance(media_items, list):
            continue
        for item in media_items:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename")
            if not filename:
                continue
            subfolder = _normalize_rel_path(item.get("subfolder", ""))
            media_type = item.get("type", "output")
            relative = Path("comfyui") / media_type
            if subfolder:
                relative = relative / subfolder
            relative = relative / filename
            items.append(
                {
                    "node_id": str(node_id),
                    "media_key": media_key,
                    "relative_path": str(relative),
                    "filename": filename,
                    "type": media_type,
                    "subfolder": subfolder,
                }
            )
    return items


def _public_comfyui_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if key != COMFYUI_INTERNAL_DEBUG_PARAM}


def _comfyui_debug_dump_enabled(params: dict[str, Any]) -> bool:
    return bool(params.get(COMFYUI_INTERNAL_DEBUG_PARAM))


def _comfyui_debug_dump_path(job_id: str) -> Path:
    return COMFYUI_DEBUG_DIR / f"{job_id}.patched.json"


def _serialize_comfyui_debug_dump_path(job_id: str, params: dict[str, Any]) -> Optional[str]:
    path = _comfyui_debug_dump_path(job_id)
    if _comfyui_debug_dump_enabled(params) or path.exists():
        return str(path)
    return None


def _extract_prompt(service: str, payload: dict[str, Any]) -> Optional[str]:
    if service in TTS_PROVIDER_IDS:
        return payload.get("text")
    if service in {"wan21", "reactor"}:
        return payload.get("prompt")
    if service in {"comfyui"}:
        return payload.get("template_name") or payload.get("template_id")
    if service in {"liveportrait", "lipsync"}:
        return payload.get("prompt") or payload.get("description")
    return payload.get("prompt")


def _compact_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    compact = dict(payload)
    # Workflows can be huge; keep history metadata compact.
    if "workflow" in compact:
        compact["workflow"] = "<omitted>"
    if "prompt_graph" in compact:
        compact["prompt_graph"] = "<omitted>"
    return compact


def persist_generation_record(
    *,
    job_id: str,
    service: str,
    payload: dict[str, Any],
    output_path: str,
    model_used: Optional[str] = None,
)-> str:
    normalized_output = normalize_output_path(output_path)
    prompt = _extract_prompt(service, payload)
    params = _compact_parameters(payload)

    record = GenerationHistory(
        id=str(uuid.uuid4()),
        job_id=job_id,
        service=service,
        model_used=model_used or MODEL_NAME_BY_SERVICE.get(service, service),
        prompt=prompt,
        parameters_json=json.dumps(params, ensure_ascii=False),
        output_path=normalized_output,
        created_at=_now_utc(),
    )
    with SessionLocal() as db:
        db.add(record)
        db.commit()
    return record.id


def serialize_generation(record: GenerationHistory) -> dict[str, Any]:
    return {
        "id": record.id,
        "job_id": record.job_id,
        "service": record.service,
        "model_used": record.model_used,
        "prompt": record.prompt,
        "parameters": _safe_json_loads(record.parameters_json, {}),
        "timestamp": record.created_at.isoformat(),
        "output_path": record.output_path,
        "download_url": f"/api/v1/history/{record.id}/download",
        "preview_url": f"/api/v1/outputs/{record.output_path}",
    }


def serialize_preset(record: PresetProfile) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "tool": record.tool,
        "state": _safe_json_loads(record.state_json, {}),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def list_asset_files(asset_dir: Path, extensions: set[str]) -> list[dict[str, Any]]:
    if not asset_dir.exists():
        return []

    items: list[dict[str, Any]] = []
    for path in sorted(asset_dir.rglob("*")):
        if not path.is_file():
            continue
        if extensions and path.suffix.lower() not in extensions:
            continue
        resolved = _ensure_within_root(path, asset_dir)
        stat = resolved.stat()
        items.append(
            {
                "name": resolved.name,
                "path": str(resolved),
                "relative_path": str(resolved.relative_to(asset_dir.resolve())),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return items


def _form_text(form: Any, key: str, default: str = "") -> str:
    value = form.get(key, default)
    if hasattr(value, "filename"):
        return default
    if value is None:
        return default
    return str(value)


async def _read_upload_file(upload: UploadFile | None) -> tuple[str, bytes, str] | None:
    if upload is None or not upload.filename:
        return None

    try:
        content = await upload.read()
    finally:
        await upload.close()

    if not content:
        return None

    content_type = upload.content_type or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream"
    return (upload.filename, content, content_type)


def _read_asset_audio(path_value: str | None) -> tuple[str, bytes, str] | None:
    if not path_value:
        return None
    asset_path = resolve_asset_path(path_value, VOICE_ASSETS_DIR)
    return (
        asset_path.name,
        asset_path.read_bytes(),
        mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream",
    )


async def parse_tts_job_submission(
    request: Request,
    *,
    default_provider: str = "f5tts",
) -> tuple[TTSJobCreateRequest, TTSUploadBundle]:
    content_type = request.headers.get("content-type", "")

    try:
        if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            payload = TTSJobCreateRequest.model_validate(
                {
                    "provider": _form_text(form, "provider", default_provider) or default_provider,
                    "text": _form_text(form, "text"),
                    "speaker_name": _form_text(form, "speaker_name") or None,
                    "reference_audio_path": _form_text(form, "reference_audio_path")
                    or _form_text(form, "saved_voice_path")
                    or None,
                    "reference_text": _form_text(form, "reference_text") or _form_text(form, "ref_text") or None,
                    "continuation_audio_path": _form_text(form, "continuation_audio_path") or None,
                    "transcript": _form_text(form, "transcript") or None,
                    "style_prompt": _form_text(form, "style_prompt") or None,
                    "target_sample_rate": _form_text(form, "target_sample_rate") or None,
                    "output_format": _form_text(form, "output_format") or _form_text(form, "format") or None,
                    "options": _form_text(form, "options") or None,
                    "speed": _form_text(form, "speed") or None,
                }
            )

            reference_upload = await _read_upload_file(
                form.get("reference_audio") if form.get("reference_audio") else form.get("ref_audio")
            )
            continuation_upload = await _read_upload_file(
                form.get("continuation_audio") if form.get("continuation_audio") else form.get("seed_audio")
            )
        else:
            payload = TTSJobCreateRequest.model_validate(await request.json())
            reference_upload = None
            continuation_upload = None
    except Exception as exc:
        raise HTTPException(400, f"Invalid TTS request: {exc}") from exc

    if reference_upload and payload.reference_audio_path:
        raise HTTPException(400, "Choose either reference_audio upload or reference_audio_path, not both.")
    if continuation_upload and payload.continuation_audio_path:
        raise HTTPException(400, "Choose either continuation_audio upload or continuation_audio_path, not both.")

    uploads = TTSUploadBundle(
        reference_audio=reference_upload or _read_asset_audio(payload.reference_audio_path),
        continuation_audio=continuation_upload or _read_asset_audio(payload.continuation_audio_path),
    )
    return payload, uploads


async def create_tts_job_from_request(
    request: Request,
    *,
    default_provider: str = "f5tts",
) -> dict[str, Any]:
    payload, uploads = await parse_tts_job_submission(request, default_provider=default_provider)
    provider = TTS_PROVIDER_REGISTRY.get(payload.provider)
    result = await provider.execute(payload, request, uploads)
    if isinstance(result, dict):
        result.setdefault("provider", provider.provider_id)
    return result


# ---------------------------------------------------------------------------
# Job Queue
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobRecord(BaseModel):
    job_id: str
    service: str
    status: JobStatus
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result_path: Optional[str] = None
    debug_dump_path: Optional[str] = None
    debug_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None
    error: Optional[str] = None


@dataclass
class TTSUploadBundle:
    reference_audio: tuple[str, bytes, str] | None = None
    continuation_audio: tuple[str, bytes, str] | None = None


class TTSProviderCapabilities(BaseModel):
    basic_tts: bool = True
    voice_clone: bool = False
    style_prompt: bool = False
    continuation_edit: bool = False
    transcript_guided_continuation: bool = False
    voice_conversion: bool = False
    supports_reference_audio: bool = False
    supports_multi_speaker: bool = False
    supports_48khz_output: bool = False


class TTSProviderOptionField(BaseModel):
    id: str
    label: str
    type: str
    description: str = ""
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None


class TTSJobCreateRequest(BaseModel):
    provider: str = Field(default="f5tts", min_length=1, max_length=64)
    text: str = Field(min_length=1)
    speaker_name: str | None = None
    reference_audio_path: str | None = None
    reference_text: str | None = None
    continuation_audio_path: str | None = None
    transcript: str | None = None
    style_prompt: str | None = None
    target_sample_rate: int | None = Field(default=None, ge=8000, le=192000)
    output_format: str | None = Field(default=None, min_length=2, max_length=16)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        payload.setdefault("provider", "f5tts")

        if not payload.get("reference_audio_path") and payload.get("saved_voice_path"):
            payload["reference_audio_path"] = payload.get("saved_voice_path")
        if not payload.get("reference_audio_path") and payload.get("ref_audio_path"):
            payload["reference_audio_path"] = payload.get("ref_audio_path")
        if not payload.get("reference_text") and payload.get("ref_text"):
            payload["reference_text"] = payload.get("ref_text")
        if not payload.get("output_format") and payload.get("format"):
            payload["output_format"] = payload.get("format")

        raw_options = payload.get("options")
        if isinstance(raw_options, str):
            try:
                payload["options"] = json.loads(raw_options)
            except json.JSONDecodeError as exc:
                raise ValueError("options must be valid JSON") from exc
        elif raw_options is None:
            payload["options"] = {}

        if payload.get("speed") not in (None, ""):
            payload.setdefault("options", {})
            payload["options"]["speed"] = payload.get("speed")

        return payload


class TTSProviderDescriptor(BaseModel):
    provider_id: str
    display_name: str
    service: str
    enabled: bool
    description: str = ""
    gpu_tier: str = "medium"
    default_output_format: str = "wav"
    supported_output_formats: list[str] = Field(default_factory=lambda: ["wav"])
    default_target_sample_rate: int | None = None
    supported_target_sample_rates: list[int] = Field(default_factory=list)
    capabilities: TTSProviderCapabilities
    option_fields: list[TTSProviderOptionField] = Field(default_factory=list)


class TTSProviderHealth(BaseModel):
    provider_id: str
    display_name: str
    enabled: bool
    service: str
    alive: bool
    ready: bool
    status: str
    detail: str | None = None
    service_url: str | None = None
    ready_payload: dict[str, Any] = Field(default_factory=dict)


class TTSProvider(ABC):
    def __init__(
        self,
        *,
        provider_id: str,
        display_name: str,
        service_name: str,
        enabled: bool,
        description: str,
        capabilities: TTSProviderCapabilities,
        gpu_tier: str = "medium",
        supported_output_formats: list[str] | None = None,
        default_output_format: str = "wav",
        supported_target_sample_rates: list[int] | None = None,
        default_target_sample_rate: int | None = None,
        option_fields: list[TTSProviderOptionField] | None = None,
    ):
        self.provider_id = provider_id
        self.display_name = display_name
        self.service_name = service_name
        self.enabled = enabled
        self.description = description
        self.capabilities = capabilities
        self.gpu_tier = gpu_tier
        self.supported_output_formats = supported_output_formats or [default_output_format]
        self.default_output_format = default_output_format
        self.supported_target_sample_rates = supported_target_sample_rates or []
        self.default_target_sample_rate = default_target_sample_rate
        self.option_fields = option_fields or []

    def serialize(self) -> dict[str, Any]:
        return TTSProviderDescriptor(
            provider_id=self.provider_id,
            display_name=self.display_name,
            service=self.service_name,
            enabled=self.enabled,
            description=self.description,
            gpu_tier=self.gpu_tier,
            default_output_format=self.default_output_format,
            supported_output_formats=self.supported_output_formats,
            default_target_sample_rate=self.default_target_sample_rate,
            supported_target_sample_rates=self.supported_target_sample_rates,
            capabilities=self.capabilities,
            option_fields=self.option_fields,
        ).model_dump()

    def list_capabilities(self) -> dict[str, Any]:
        return self.capabilities.model_dump()

    async def health_check(self) -> dict[str, Any]:
        if not self.enabled:
            return TTSProviderHealth(
                provider_id=self.provider_id,
                display_name=self.display_name,
                enabled=False,
                service=self.service_name,
                alive=False,
                ready=False,
                status="disabled",
                detail="Provider is disabled by configuration.",
                service_url=SERVICE_URLS.get(self.service_name),
            ).model_dump()

        url = SERVICE_URLS.get(self.service_name)
        if not url:
            return TTSProviderHealth(
                provider_id=self.provider_id,
                display_name=self.display_name,
                enabled=True,
                service=self.service_name,
                alive=False,
                ready=False,
                status="unconfigured",
                detail="Service URL is not configured.",
            ).model_dump()

        if http_client is None:
            return TTSProviderHealth(
                provider_id=self.provider_id,
                display_name=self.display_name,
                enabled=True,
                service=self.service_name,
                alive=False,
                ready=False,
                status="starting",
                detail="HTTP client is not initialized yet.",
                service_url=url,
            ).model_dump()

        alive = False
        ready = False
        ready_payload: dict[str, Any] = {}
        detail: str | None = None
        status = "offline"

        try:
            response = await http_client.get(f"{url.rstrip('/')}/healthz", timeout=3.0)
            alive = response.status_code == 200
        except Exception as exc:
            detail = str(exc)

        if alive:
            try:
                ready_response = await http_client.get(f"{url.rstrip('/')}/readyz", timeout=3.0)
                ready = ready_response.status_code == 200
                ready_payload = ready_response.json() if ready_response.content else {}
            except Exception as exc:
                detail = str(exc)

        if ready:
            status = "ready"
        elif alive:
            status = "alive"

        return TTSProviderHealth(
            provider_id=self.provider_id,
            display_name=self.display_name,
            enabled=True,
            service=self.service_name,
            alive=alive,
            ready=ready,
            status=status,
            detail=detail,
            service_url=url,
            ready_payload=ready_payload,
        ).model_dump()

    def validate_request(self, payload: TTSJobCreateRequest, uploads: TTSUploadBundle) -> None:
        if not self.enabled:
            raise HTTPException(404, f"TTS provider '{self.provider_id}' is disabled.")
        if not payload.text.strip():
            raise HTTPException(400, "text is required")

        if payload.style_prompt and not self.capabilities.style_prompt:
            raise HTTPException(400, f"Provider '{self.provider_id}' does not support style_prompt.")

        if payload.reference_text and not self.capabilities.supports_reference_audio:
            raise HTTPException(400, f"Provider '{self.provider_id}' does not accept reference audio metadata.")

        if (uploads.reference_audio or payload.reference_audio_path) and not self.capabilities.supports_reference_audio:
            raise HTTPException(400, f"Provider '{self.provider_id}' does not support reference audio.")

        if (uploads.continuation_audio or payload.continuation_audio_path) and not self.capabilities.continuation_edit:
            raise HTTPException(400, f"Provider '{self.provider_id}' does not support continuation/edit audio.")

        if payload.transcript and not self.capabilities.transcript_guided_continuation:
            raise HTTPException(400, f"Provider '{self.provider_id}' does not support transcript-guided continuation.")

        if payload.target_sample_rate == 48000 and not self.capabilities.supports_48khz_output:
            raise HTTPException(400, f"Provider '{self.provider_id}' does not advertise 48 kHz output support.")

        if payload.output_format:
            normalized_format = payload.output_format.lower()
            if normalized_format not in {fmt.lower() for fmt in self.supported_output_formats}:
                raise HTTPException(
                    400,
                    f"Unsupported output_format '{payload.output_format}' for provider '{self.provider_id}'.",
                )

        if payload.target_sample_rate and self.supported_target_sample_rates:
            if payload.target_sample_rate not in self.supported_target_sample_rates:
                raise HTTPException(
                    400,
                    f"Unsupported target_sample_rate '{payload.target_sample_rate}' for provider '{self.provider_id}'.",
                )

    def operation_for(self, payload: TTSJobCreateRequest, uploads: TTSUploadBundle) -> str:
        if payload.transcript or uploads.continuation_audio or payload.continuation_audio_path:
            return "edit_or_continue"
        if uploads.reference_audio or payload.reference_audio_path:
            return "clone_voice"
        return "synthesize"

    async def execute(
        self,
        payload: TTSJobCreateRequest,
        request: Request,
        uploads: TTSUploadBundle,
    ) -> dict[str, Any]:
        self.validate_request(payload, uploads)
        operation = self.operation_for(payload, uploads)
        if operation == "edit_or_continue":
            return await self.edit_or_continue(payload, request, uploads)
        if operation == "clone_voice":
            return await self.clone_voice(payload, request, uploads)
        return await self.synthesize(payload, request, uploads)

    @abstractmethod
    async def synthesize(
        self,
        payload: TTSJobCreateRequest,
        request: Request,
        uploads: TTSUploadBundle,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def clone_voice(
        self,
        payload: TTSJobCreateRequest,
        request: Request,
        uploads: TTSUploadBundle,
    ) -> dict[str, Any]:
        if not self.capabilities.voice_clone:
            raise HTTPException(400, f"Provider '{self.provider_id}' does not support voice cloning.")
        return await self.synthesize(payload, request, uploads)

    async def edit_or_continue(
        self,
        payload: TTSJobCreateRequest,
        request: Request,
        uploads: TTSUploadBundle,
    ) -> dict[str, Any]:
        raise HTTPException(400, f"Provider '{self.provider_id}' does not support continuation or editing.")

    async def convert_voice(
        self,
        payload: TTSJobCreateRequest,
        request: Request,
        uploads: TTSUploadBundle,
    ) -> dict[str, Any]:
        raise HTTPException(400, f"Provider '{self.provider_id}' does not support voice conversion.")


class ServiceBackedTTSProvider(TTSProvider):
    def _tier_kwargs(self) -> dict[str, bool]:
        return {
            "is_gpu_heavy": self.gpu_tier == "heavy",
            "is_gpu_medium": self.gpu_tier == "medium",
        }

    def _multipart_data(self, payload: TTSJobCreateRequest) -> dict[str, Any]:
        data: dict[str, Any] = {
            "provider": payload.provider,
            "text": payload.text,
            "speaker_name": payload.speaker_name or "",
            "reference_audio_path": payload.reference_audio_path or "",
            "reference_text": payload.reference_text or "",
            "continuation_audio_path": payload.continuation_audio_path or "",
            "transcript": payload.transcript or "",
            "style_prompt": payload.style_prompt or "",
            "target_sample_rate": str(payload.target_sample_rate or ""),
            "output_format": payload.output_format or self.default_output_format,
        }
        if payload.options:
            data["options"] = json.dumps(payload.options, ensure_ascii=False)
        return data

    def _json_payload(self, payload: TTSJobCreateRequest) -> dict[str, Any]:
        body = payload.model_dump()
        body["output_format"] = payload.output_format or self.default_output_format
        return body

    def _files_payload(self, uploads: TTSUploadBundle) -> dict[str, tuple[str, bytes, str]]:
        files: dict[str, tuple[str, bytes, str]] = {}
        if uploads.reference_audio:
            files["reference_audio"] = uploads.reference_audio
        if uploads.continuation_audio:
            files["continuation_audio"] = uploads.continuation_audio
        return files

    async def _submit(
        self,
        payload: TTSJobCreateRequest,
        request: Request,
        uploads: TTSUploadBundle,
        path: str,
    ) -> dict[str, Any]:
        files = self._files_payload(uploads)
        if files:
            return await proxy_to_service(
                self.service_name,
                path,
                request,
                files=files,
                data=self._multipart_data(payload),
                public_service=self.provider_id,
                history_model_used=self.display_name,
                **self._tier_kwargs(),
            )
        return await proxy_to_service(
            self.service_name,
            path,
            request,
            json_body=self._json_payload(payload),
            public_service=self.provider_id,
            history_model_used=self.display_name,
            **self._tier_kwargs(),
        )

    async def synthesize(
        self,
        payload: TTSJobCreateRequest,
        request: Request,
        uploads: TTSUploadBundle,
    ) -> dict[str, Any]:
        return await self._submit(payload, request, uploads, "/synthesize")


class F5TTSProvider(TTSProvider):
    def __init__(self):
        super().__init__(
            provider_id="f5tts",
            display_name="F5-TTS",
            service_name="f5tts",
            enabled=True,
            description="Existing warm-tier F5-TTS integration.",
            capabilities=TTSProviderCapabilities(
                basic_tts=True,
                voice_clone=True,
                supports_reference_audio=True,
            ),
            gpu_tier="medium",
            supported_output_formats=["wav"],
            default_output_format="wav",
            supported_target_sample_rates=[int(os.getenv("F5TTS_SAMPLE_RATE", "24000"))],
            default_target_sample_rate=int(os.getenv("F5TTS_SAMPLE_RATE", "24000")),
            option_fields=[
                TTSProviderOptionField(
                    id="speed",
                    label="Speed",
                    type="number",
                    description="Playback speed multiplier for F5-TTS.",
                    default=1.0,
                    min=0.5,
                    max=2.0,
                    step=0.05,
                )
            ],
        )

    def validate_request(self, payload: TTSJobCreateRequest, uploads: TTSUploadBundle) -> None:
        super().validate_request(payload, uploads)
        if payload.target_sample_rate and payload.target_sample_rate != self.default_target_sample_rate:
            raise HTTPException(
                400,
                f"F5-TTS currently outputs {self.default_target_sample_rate} Hz audio only.",
            )

    async def synthesize(
        self,
        payload: TTSJobCreateRequest,
        request: Request,
        uploads: TTSUploadBundle,
    ) -> dict[str, Any]:
        speed = payload.options.get("speed", 1.0)
        try:
            speed_value = float(speed)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "options.speed must be numeric") from exc

        files: dict[str, tuple[str, bytes, str]] | None = None
        if uploads.reference_audio:
            files = {"ref_audio": uploads.reference_audio}

        data = {
            "text": payload.text,
            "ref_text": payload.reference_text or "",
            "speed": speed_value,
        }

        if files:
            return await proxy_to_service(
                "f5tts",
                "/synthesize",
                request,
                files=files,
                data=data,
                public_service=self.provider_id,
                history_model_used=self.display_name,
                is_gpu_medium=True,
            )

        return await proxy_to_service(
            "f5tts",
            "/synthesize",
            request,
            json_body=data,
            public_service=self.provider_id,
            history_model_used=self.display_name,
            is_gpu_medium=True,
        )


class TTSProviderRegistry:
    def __init__(self, providers: list[TTSProvider]):
        self._providers = {provider.provider_id: provider for provider in providers}

    def list(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        providers = list(self._providers.values())
        if not include_disabled:
            providers = [provider for provider in providers if provider.enabled]
        return [provider.serialize() for provider in providers]

    def get(self, provider_id: str) -> TTSProvider:
        provider = self._providers.get(provider_id)
        if not provider:
            raise HTTPException(404, f"Unknown TTS provider '{provider_id}'.")
        return provider

    def ids(self) -> set[str]:
        return set(self._providers.keys())


def _tts_capabilities_from_env(prefix: str, defaults: dict[str, bool]) -> TTSProviderCapabilities:
    return TTSProviderCapabilities(
        basic_tts=_env_bool(f"{prefix}_BASIC_TTS", defaults.get("basic_tts", True)),
        voice_clone=_env_bool(f"{prefix}_VOICE_CLONE", defaults.get("voice_clone", False)),
        style_prompt=_env_bool(f"{prefix}_STYLE_PROMPT", defaults.get("style_prompt", False)),
        continuation_edit=_env_bool(
            f"{prefix}_CONTINUATION_EDIT",
            defaults.get("continuation_edit", False),
        ),
        transcript_guided_continuation=_env_bool(
            f"{prefix}_TRANSCRIPT_GUIDED_CONTINUATION",
            defaults.get("transcript_guided_continuation", False),
        ),
        voice_conversion=_env_bool(f"{prefix}_VOICE_CONVERSION", defaults.get("voice_conversion", False)),
        supports_reference_audio=_env_bool(
            f"{prefix}_SUPPORTS_REFERENCE_AUDIO",
            defaults.get("supports_reference_audio", False),
        ),
        supports_multi_speaker=_env_bool(
            f"{prefix}_SUPPORTS_MULTI_SPEAKER",
            defaults.get("supports_multi_speaker", False),
        ),
        supports_48khz_output=_env_bool(
            f"{prefix}_SUPPORTS_48KHZ_OUTPUT",
            defaults.get("supports_48khz_output", False),
        ),
    )


def build_tts_provider_registry() -> TTSProviderRegistry:
    fish_provider = ServiceBackedTTSProvider(
        provider_id="fish_speech",
        display_name=os.getenv("FISH_SPEECH_DISPLAY_NAME", "Fish Speech"),
        service_name="fish_speech",
        enabled=_env_bool("FISH_SPEECH_ENABLED", False),
        description="Adapter for a Fish Speech-compatible upstream runtime.",
        capabilities=_tts_capabilities_from_env(
            "FISH_SPEECH",
            {
                "basic_tts": True,
                "voice_clone": True,
                "supports_reference_audio": True,
            },
        ),
        gpu_tier="medium",
        supported_output_formats=_parse_str_list_env(
            os.getenv("FISH_SPEECH_OUTPUT_FORMATS"),
            ["wav", "mp3", "opus"],
        ),
        default_output_format=os.getenv("FISH_SPEECH_DEFAULT_OUTPUT_FORMAT", "wav"),
        supported_target_sample_rates=_parse_int_list_env(
            os.getenv("FISH_SPEECH_TARGET_SAMPLE_RATES"),
            [],
        ),
        default_target_sample_rate=None,
    )

    premium_provider = ServiceBackedTTSProvider(
        provider_id="premium_clone_tts",
        display_name=os.getenv("PREMIUM_CLONE_TTS_DISPLAY_NAME", "Premium Clone TTS"),
        service_name="premium_clone_tts",
        enabled=_env_bool("PREMIUM_CLONE_TTS_ENABLED", False),
        description="Scaffolded premium cloning slot with a pass-through adapter for future runtime wiring.",
        capabilities=_tts_capabilities_from_env(
            "PREMIUM_CLONE_TTS",
            {
                "basic_tts": True,
                "voice_clone": True,
                "supports_reference_audio": True,
            },
        ),
        gpu_tier="medium",
        supported_output_formats=_parse_str_list_env(
            os.getenv("PREMIUM_CLONE_TTS_OUTPUT_FORMATS"),
            ["wav"],
        ),
        default_output_format=os.getenv("PREMIUM_CLONE_TTS_DEFAULT_OUTPUT_FORMAT", "wav"),
        supported_target_sample_rates=_parse_int_list_env(
            os.getenv("PREMIUM_CLONE_TTS_TARGET_SAMPLE_RATES"),
            [],
        ),
        default_target_sample_rate=None,
    )

    return TTSProviderRegistry(
        [
            F5TTSProvider(),
            fish_provider,
            premium_provider,
        ]
    )


TTS_PROVIDER_REGISTRY = build_tts_provider_registry()
TTS_PROVIDER_IDS = TTS_PROVIDER_REGISTRY.ids()


class PresetUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    tool: str = Field(min_length=1, max_length=64)
    state: dict[str, Any] = Field(default_factory=dict)


class ReactorGenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    lora_path: str | None = None
    lora_strength: float = 0.75
    workflow: dict[str, Any] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ComfyUITemplateInputSpec(BaseModel):
    id: str
    label: str
    kind: str
    description: str = ""
    accepted_extensions: list[str] = Field(default_factory=list)
    max_size_mb: int = 0


class ComfyUITemplateParamSpec(BaseModel):
    id: str
    label: str
    type: str
    description: str = ""
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None


class ComfyUIRuntimeMapping(BaseModel):
    input_id: str
    node_id: str
    input_name: str
    value_source: str = "param"
    value: Any = None


class ComfyUIOutputSpec(BaseModel):
    node_id: Optional[str] = None
    node_type: Optional[str] = None
    media_keys: list[str] = Field(default_factory=lambda: ["gifs", "videos", "images", "audio", "files"])


class ComfyUITemplateManifest(BaseModel):
    id: str
    name: str
    description: str
    category: str
    workflow_file: str
    workflow_format: str = "api"
    gpu_tier: str = "heavy"
    required_inputs: list[ComfyUITemplateInputSpec]
    optional_params: list[ComfyUITemplateParamSpec] = Field(default_factory=list)
    runtime_mappings: list[ComfyUIRuntimeMapping] = Field(default_factory=list)
    output_type: str = "video"
    output: ComfyUIOutputSpec


class ComfyUIAssetUploadResponse(BaseModel):
    id: str
    kind: str
    original_filename: str
    stored_filename: str
    relative_path: str
    content_type: Optional[str] = None
    size_bytes: int
    created_at: str


class ComfyUIJobCreateRequest(BaseModel):
    template_id: str = Field(min_length=1, max_length=128)
    inputs: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    debug_dump: bool = False


_template_manifest_cache: Optional[list[ComfyUITemplateManifest]] = None
_template_manifest_cache_key: Optional[tuple[str, ...]] = None
_template_api_cache: dict[tuple[str, float], dict[str, Any]] = {}

MODEL_CATEGORY_BY_NODE_TYPE = {
    "CLIPLoader": "text_encoder",
    "CLIPVisionLoader": "clip_vision",
    "DownloadAndLoadSAM2Model": "sam2",
    "LoraLoaderModelOnly": "lora",
    "OnnxDetectionModelLoader": "preprocess",
    "UNETLoader": "diffusion_model",
    "VAELoader": "vae",
    "VHS_LoadVideo": "video_input",
    "WanVideoLoraSelectMulti": "lora",
    "WanVideoModelLoader": "diffusion_model",
    "WanVideoTextEncodeCached": "text_encoder",
    "WanVideoVAELoader": "vae",
}

MODEL_KEYWORDS_BY_INPUT = {
    "bbox_detector": "preprocess",
    "clip_name": "text_encoder",
    "image": "image_input",
    "lora_name": "lora",
    "model": "model",
    "pose_estimator": "preprocess",
    "sam_model": "sam2",
    "sam2_model": "sam2",
    "text_encoder": "text_encoder",
    "unet_name": "diffusion_model",
    "vae_name": "vae",
    "video": "video_input",
}


def _normalize_rel_path(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def _workflow_file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "asset").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._-") or "asset"
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", Path(name).suffix.lower())
    return f"{stem}{suffix}"


def guess_comfyui_asset_kind(filename: str, content_type: Optional[str] = None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in COMFYUI_IMAGE_EXTENSIONS or (content_type or "").startswith("image/"):
        return "image"
    if suffix in COMFYUI_VIDEO_EXTENSIONS or (content_type or "").startswith("video/"):
        return "video"
    raise HTTPException(400, "Unsupported asset type. Upload an image or video file.")


def comfyui_asset_size_limit_bytes(kind: str) -> int:
    if kind == "image":
        return COMFYUI_MAX_IMAGE_MB * 1024 * 1024
    if kind == "video":
        return COMFYUI_MAX_VIDEO_MB * 1024 * 1024
    return COMFYUI_MAX_VIDEO_MB * 1024 * 1024


def serialize_comfyui_asset(record: ComfyUIAssetRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "kind": record.kind,
        "original_filename": record.original_filename,
        "stored_filename": record.stored_filename,
        "relative_path": record.relative_path,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "created_at": record.created_at.isoformat(),
    }


def serialize_comfyui_job_record(record: ComfyUIJobRecordDB) -> dict[str, Any]:
    params = _safe_json_loads(record.params_json, {})
    return {
        "job_id": record.id,
        "template_id": record.template_id,
        "template_name": record.template_name,
        "status": record.status,
        "message": record.status_message,
        "prompt_id": record.prompt_id,
        "created_at": record.created_at.isoformat(),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "result_path": record.output_path,
        "debug_dump_path": _serialize_comfyui_debug_dump_path(record.id, params),
        "debug_artifacts": _safe_json_loads(record.artifacts_json, []),
        "history_id": record.history_id,
        "error": record.error,
        "inputs": _safe_json_loads(record.inputs_json, {}),
        "params": _public_comfyui_params(params),
        "validation": _safe_json_loads(record.validation_json, {}),
    }


def _template_cache_signature() -> tuple[str, ...]:
    if not COMFYUI_TEMPLATE_DIR.exists():
        return ()
    signatures: list[str] = []
    for path in sorted(COMFYUI_TEMPLATE_DIR.glob("*.manifest.json")):
        signatures.append(f"{path.name}:{_workflow_file_mtime(path)}")
    return tuple(signatures)


def load_comfyui_templates() -> list[ComfyUITemplateManifest]:
    global _template_manifest_cache, _template_manifest_cache_key

    cache_key = _template_cache_signature()
    if _template_manifest_cache is not None and cache_key == _template_manifest_cache_key:
        return _template_manifest_cache

    manifests: list[ComfyUITemplateManifest] = []
    if COMFYUI_TEMPLATE_DIR.exists():
        for manifest_path in sorted(COMFYUI_TEMPLATE_DIR.glob("*.manifest.json")):
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = ComfyUITemplateManifest.model_validate(raw)
            manifests.append(manifest)

    _template_manifest_cache = manifests
    _template_manifest_cache_key = cache_key
    return manifests


def get_comfyui_template(template_id: str) -> ComfyUITemplateManifest:
    for manifest in load_comfyui_templates():
        if manifest.id == template_id:
            return manifest
    raise HTTPException(
        404,
        _comfyui_error_detail("Unknown ComfyUI template.", template_id=template_id),
    )


def resolve_template_workflow_path(manifest: ComfyUITemplateManifest) -> Path:
    workflow_path = COMFYUI_TEMPLATE_DIR / manifest.workflow_file
    if not workflow_path.exists():
        raise HTTPException(500, f"Template workflow is missing: {manifest.workflow_file}")
    return workflow_path


def load_template_workflow(manifest: ComfyUITemplateManifest) -> dict[str, Any]:
    workflow_path = resolve_template_workflow_path(manifest)
    return json.loads(workflow_path.read_text(encoding="utf-8"))


def workflow_is_api_format(workflow: dict[str, Any]) -> bool:
    if "nodes" in workflow and "links" in workflow:
        return False
    return all(
        isinstance(key, str) and isinstance(value, dict) and "class_type" in value
        for key, value in workflow.items()
    )


def _serialize_prompt_graph_for_submission(prompt_graph: dict[str, Any]) -> str:
    return json.dumps(prompt_graph, ensure_ascii=False, separators=(",", ":"))


def _build_comfyui_prompt_request_body(
    *,
    client_id: str,
    prompt_json: str,
    job_id: str,
    manifest: "ComfyUITemplateManifest",
) -> str:
    extra_data_json = json.dumps(
        {
            "neonforge": {
                "job_id": job_id,
                "template_id": manifest.id,
                "template_name": manifest.name,
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "{"
        f'"client_id":{json.dumps(client_id, ensure_ascii=False)},'
        f'"prompt":{prompt_json},'
        f'"extra_data":{extra_data_json}'
        "}"
    )


def _workflow_node_subset(prompt_graph: dict[str, Any], node_ids: tuple[str, ...]) -> dict[str, Any]:
    subset: dict[str, Any] = {}
    for node_id in node_ids:
        node = prompt_graph.get(str(node_id))
        if node is not None:
            subset[str(node_id)] = node
    return subset


def _workflow_node_input_value(prompt_graph: dict[str, Any], node_id: str, input_name: str) -> Any:
    node = prompt_graph.get(str(node_id))
    if not isinstance(node, dict):
        return None
    node_inputs = node.get("inputs")
    if not isinstance(node_inputs, dict):
        return None
    return node_inputs.get(input_name)


def _build_wan_character_swap_manual_reference_subset(base_prompt_graph: dict[str, Any]) -> dict[str, Any]:
    reference = json.loads(json.dumps(base_prompt_graph))

    node27 = reference.get("27")
    if isinstance(node27, dict):
        node27_inputs = node27.setdefault("inputs", {})
        if isinstance(node27_inputs, dict):
            node27_inputs["steps"] = 4

    node30 = reference.get("30")
    if isinstance(node30, dict):
        node30_inputs = node30.setdefault("inputs", {})
        if isinstance(node30_inputs, dict):
            node30_inputs["images"] = ["42", 0]

    node107 = reference.get("107")
    if isinstance(node107, dict):
        node107_inputs = node107.setdefault("inputs", {})
        if isinstance(node107_inputs, dict):
            _set_points_editor_inputs(
                node107_inputs,
                positive_points=WAN_CHARACTER_SWAP_MANUAL_POSITIVE_POINTS,
                negative_points=WAN_CHARACTER_SWAP_MANUAL_NEGATIVE_POINTS,
            )

    node120 = reference.get("120")
    if isinstance(node120, dict):
        node120_inputs = node120.setdefault("inputs", {})
        if isinstance(node120_inputs, dict):
            node120_inputs["person_index"] = 0

    return _workflow_node_subset(reference, WAN_CHARACTER_SWAP_DIFF_NODE_IDS)


def _diff_workflow_node_subsets(reference_subset: dict[str, Any], runtime_subset: dict[str, Any]) -> str:
    reference_json = json.dumps(reference_subset, ensure_ascii=False, indent=2, sort_keys=True)
    runtime_json = json.dumps(runtime_subset, ensure_ascii=False, indent=2, sort_keys=True)
    diff_lines = list(
        difflib.unified_diff(
            reference_json.splitlines(),
            runtime_json.splitlines(),
            fromfile="manual-working-reference",
            tofile="runtime-submitted",
            lineterm="",
        )
    )
    return "\n".join(diff_lines) if diff_lines else "(no diff)"


def _log_wan_character_swap_prompt_diagnostics(
    patched_prompt: dict[str, Any],
    *,
    base_prompt_graph: dict[str, Any],
    job_id: str,
    prompt_sha256: str,
    debug_dump_path: Optional[str],
):
    final_values = {
        "node_27.steps": _workflow_node_input_value(patched_prompt, "27", "steps"),
        "node_27.denoise_strength": _workflow_node_input_value(patched_prompt, "27", "denoise_strength"),
        "node_107.points_store": _workflow_node_input_value(patched_prompt, "107", "points_store"),
        "node_120.person_index": _workflow_node_input_value(patched_prompt, "120", "person_index"),
        "node_30.images": _workflow_node_input_value(patched_prompt, "30", "images"),
    }
    log.info(
        "Wan character swap final patched values job_id=%s prompt_sha256=%s debug_dump_path=%s values=%s",
        job_id,
        prompt_sha256,
        debug_dump_path or "",
        json.dumps(final_values, ensure_ascii=False),
    )

    reference_subset = _build_wan_character_swap_manual_reference_subset(base_prompt_graph)
    runtime_subset = _workflow_node_subset(patched_prompt, WAN_CHARACTER_SWAP_DIFF_NODE_IDS)
    log.info(
        "Wan character swap limited diff job_id=%s reference_source=%s nodes=%s\n%s",
        job_id,
        "template-plus-manual-overrides",
        ",".join(WAN_CHARACTER_SWAP_DIFF_NODE_IDS),
        _diff_workflow_node_subsets(reference_subset, runtime_subset),
    )


def _collect_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            strings.extend(_collect_strings(child))
    elif isinstance(value, list):
        for child in value:
            strings.extend(_collect_strings(child))
    return strings


def _model_category(node_type: str, input_name: str) -> str:
    if input_name in MODEL_KEYWORDS_BY_INPUT:
        category = MODEL_KEYWORDS_BY_INPUT[input_name]
        if category == "model":
            return MODEL_CATEGORY_BY_NODE_TYPE.get(node_type, "model")
        return category
    return MODEL_CATEGORY_BY_NODE_TYPE.get(node_type, "model")


def _looks_like_model_reference(value: str) -> bool:
    suffix = Path(value).suffix.lower()
    return suffix in COMFYUI_MODEL_EXTENSIONS


def _append_model_reference(
    references: list[dict[str, Any]],
    *,
    node_id: str,
    node_type: str,
    input_name: str,
    value: str,
):
    normalized = _normalize_rel_path(value)
    references.append(
        {
            "node_id": str(node_id),
            "node_type": node_type,
            "input_name": input_name,
            "category": _model_category(node_type, input_name),
            "value": normalized,
            "filename": Path(normalized).name,
        }
    )


def extract_workflow_model_references(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []

    if workflow_is_api_format(workflow):
        for node_id, node in workflow.items():
            node_type = node.get("class_type", "")
            for input_name, value in (node.get("inputs") or {}).items():
                if isinstance(value, str) and _looks_like_model_reference(value):
                    _append_model_reference(
                        references,
                        node_id=str(node_id),
                        node_type=node_type,
                        input_name=input_name,
                        value=value,
                    )
        return references

    for node in workflow.get("nodes", []):
        node_id = str(node.get("id"))
        node_type = node.get("type") or node.get("class_type") or ""
        widget_values = node.get("widgets_values")

        if isinstance(widget_values, dict):
            for input_name, value in widget_values.items():
                if isinstance(value, str) and _looks_like_model_reference(value):
                    _append_model_reference(
                        references,
                        node_id=node_id,
                        node_type=node_type,
                        input_name=input_name,
                        value=value,
                    )
            continue

        if isinstance(widget_values, list):
            for index, value in enumerate(widget_values):
                if isinstance(value, str) and _looks_like_model_reference(value):
                    _append_model_reference(
                        references,
                        node_id=node_id,
                        node_type=node_type,
                        input_name=f"widget_{index}",
                        value=value,
                    )

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for ref in references:
        key = (ref["node_id"], ref["filename"], ref["category"])
        deduped.setdefault(key, ref)
    return list(deduped.values())


def scan_comfyui_models() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    by_filename: dict[str, list[dict[str, Any]]] = {}
    scanned_roots: list[dict[str, Any]] = []

    for root in COMFYUI_MODEL_ROOTS:
        root_info: Optional[dict[str, Any]] = None

        if root.exists():
            resolved_root = root.resolve(strict=False)
            root_info = {
                "path": str(root),
                "resolved_path": str(resolved_root),
                "exists": True,
                "is_dir": root.is_dir(),
                "item_count": 0,
                "error": None,
                "source": "gateway_fs",
                "container": None,
            }
            if not root_info["is_dir"]:
                root_info["error"] = "Path exists but is not a directory."
            else:
                try:
                    for path in sorted(root.rglob("*")):
                        if not path.is_file() or path.suffix.lower() not in COMFYUI_MODEL_EXTENSIONS:
                            continue
                        resolved = _ensure_within_root(path, root)
                        stat = resolved.stat()
                        item = {
                            "filename": resolved.name,
                            "path": str(resolved),
                            "relative_path": str(resolved.relative_to(root.resolve())),
                            "root": str(root),
                            "size_bytes": stat.st_size,
                            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                            "source": "gateway_fs",
                            "container": None,
                        }
                        items.append(item)
                        by_filename.setdefault(resolved.name, []).append(item)
                        root_info["item_count"] += 1
                except OSError as exc:
                    root_info["error"] = str(exc)
        else:
            try:
                with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                    response = client.get(
                        f"{SUPERVISOR_URL.rstrip('/')}/container-files/comfyui",
                        params={"root": str(root)},
                    )
                    response.raise_for_status()
                    remote_payload = response.json()
            except httpx.HTTPStatusError as exc:
                resolved_container = "ai-comfyui"
                detail_message = exc.response.text.strip() or str(exc)
                try:
                    detail = exc.response.json().get("detail")
                except (ValueError, AttributeError):
                    detail = None
                if isinstance(detail, dict):
                    resolved_container = detail.get("resolved_container", resolved_container)
                    detail_message = json.dumps(detail, ensure_ascii=False)
                root_info = {
                    "path": str(root),
                    "resolved_path": str(root),
                    "exists": False,
                    "is_dir": False,
                    "item_count": 0,
                    "error": f"ComfyUI container scan failed: {detail_message}",
                    "source": "comfyui_container",
                    "container": resolved_container,
                }
            except httpx.HTTPError as exc:
                root_info = {
                    "path": str(root),
                    "resolved_path": str(root),
                    "exists": False,
                    "is_dir": False,
                    "item_count": 0,
                    "error": f"ComfyUI container scan failed: {exc}",
                    "source": "comfyui_container",
                    "container": "ai-comfyui",
                }
            else:
                root_info = {
                    "path": remote_payload.get("path", str(root)),
                    "resolved_path": remote_payload.get("resolved_path", str(root)),
                    "exists": bool(remote_payload.get("exists")),
                    "is_dir": bool(remote_payload.get("is_dir")),
                    "item_count": int(remote_payload.get("item_count", 0)),
                    "error": remote_payload.get("error"),
                    "source": remote_payload.get("source", "comfyui_container"),
                    "container": remote_payload.get("container", "ai-comfyui"),
                }
                for remote_item in remote_payload.get("items", []):
                    item = {
                        "filename": remote_item["filename"],
                        "path": remote_item["path"],
                        "relative_path": remote_item["relative_path"],
                        "root": root_info["path"],
                        "size_bytes": remote_item["size_bytes"],
                        "modified_at": remote_item["modified_at"],
                        "source": root_info["source"],
                        "container": root_info["container"],
                    }
                    items.append(item)
                    by_filename.setdefault(item["filename"], []).append(item)

        if root_info is not None:
            scanned_roots.append(root_info)

    return {
        "roots": [str(root) for root in COMFYUI_MODEL_ROOTS],
        "scanned_roots": scanned_roots,
        "items": items,
        "by_filename": by_filename,
    }


def validate_template_models(manifest: ComfyUITemplateManifest) -> dict[str, Any]:
    workflow = load_template_workflow(manifest)
    references = extract_workflow_model_references(workflow)
    inventory = scan_comfyui_models()
    available: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    warnings: list[str] = []
    readable_roots = [root for root in inventory["scanned_roots"] if root["exists"] and root["is_dir"]]

    if not readable_roots:
        configured = ", ".join(root["path"] for root in inventory["scanned_roots"]) or "<none>"
        warnings.append(
            f"No readable ComfyUI model roots were found in the gateway filesystem or ai-comfyui container. "
            f"Configured roots: {configured}"
        )
    elif not inventory["items"]:
        configured = ", ".join(root["path"] for root in inventory["scanned_roots"])
        warnings.append(
            f"Configured ComfyUI model roots were found but no model files were discovered. Scanned roots: {configured}"
        )

    for ref in references:
        matches = inventory["by_filename"].get(ref["filename"], [])
        if matches:
            if len(matches) > 1:
                warnings.append(
                    f"Multiple installed model files match {ref['filename']}; using filename-only validation."
                )
            available.append(
                {
                    **ref,
                    "satisfied_by_root": matches[0]["root"],
                    "satisfied_by_path": matches[0]["path"],
                    "satisfied_by_source": matches[0].get("source"),
                    "matches": [
                        {
                            "relative_path": match["relative_path"],
                            "root": match["root"],
                            "path": match["path"],
                            "source": match.get("source"),
                            "container": match.get("container"),
                        }
                        for match in matches
                    ],
                }
            )
        else:
            missing.append(ref)

    return {
        "template_id": manifest.id,
        "available": available,
        "missing": missing,
        "warnings": sorted(set(warnings)),
    }


def serialize_comfyui_template(
    manifest: ComfyUITemplateManifest,
    *,
    include_workflow_file: bool = False,
) -> dict[str, Any]:
    payload = manifest.model_dump()
    if not include_workflow_file:
        payload.pop("workflow_file", None)
    return payload


def _coerce_param_value(value: Any, spec: Optional[ComfyUITemplateParamSpec]) -> Any:
    if spec is None:
        return value
    if value is None or value == "":
        return spec.default
    if spec.type == "integer":
        return int(value)
    if spec.type == "number":
        return float(value)
    if spec.type == "boolean":
        return bool(value)
    return value


def _configure_wan_character_swap_debug_outputs(
    prompt_graph: dict[str, Any],
    *,
    debug_enabled: bool,
    job_id: Optional[str],
):
    for spec in WAN_CHARACTER_SWAP_DEBUG_OUTPUTS:
        node = prompt_graph.get(spec["node_id"])
        if not node or node.get("class_type") != "VHS_VideoCombine":
            raise HTTPException(
                500,
                f"Wan Character Swap debug output node {spec['node_id']} is missing or invalid.",
            )
        node_inputs = node.setdefault("inputs", {})
        node_inputs["save_output"] = debug_enabled
        if job_id:
            node_inputs["filename_prefix"] = spec["filename_prefix"].format(job_id=job_id)


def _apply_wan_character_swap_targeting_overrides(
    prompt_graph: dict[str, Any],
    params: dict[str, Any],
):
    if (
        WAN_CHARACTER_SWAP_POSITIVE_POINTS_PARAM not in params
        and WAN_CHARACTER_SWAP_NEGATIVE_POINTS_PARAM not in params
    ):
        return

    node = prompt_graph.get("107")
    if not node or "inputs" not in node:
        raise HTTPException(500, "Wan Character Swap node 107 is missing for point overrides.")

    node_inputs = node["inputs"]
    positive_points = _load_points_editor_points(
        node_inputs,
        "coordinates",
        field_name=WAN_CHARACTER_SWAP_POSITIVE_POINTS_PARAM,
    )
    negative_points = _load_points_editor_points(
        node_inputs,
        "neg_coordinates",
        field_name=WAN_CHARACTER_SWAP_NEGATIVE_POINTS_PARAM,
    )

    if WAN_CHARACTER_SWAP_POSITIVE_POINTS_PARAM in params:
        positive_points = _parse_points_json(
            params.get(WAN_CHARACTER_SWAP_POSITIVE_POINTS_PARAM),
            field_name=WAN_CHARACTER_SWAP_POSITIVE_POINTS_PARAM,
        )
    if WAN_CHARACTER_SWAP_NEGATIVE_POINTS_PARAM in params:
        negative_points = _parse_points_json(
            params.get(WAN_CHARACTER_SWAP_NEGATIVE_POINTS_PARAM),
            field_name=WAN_CHARACTER_SWAP_NEGATIVE_POINTS_PARAM,
        )

    _set_points_editor_inputs(
        node_inputs,
        positive_points=positive_points,
        negative_points=negative_points,
    )


def _enforce_production_video_output(
    prompt_graph: dict[str, Any],
    manifest: ComfyUITemplateManifest,
    *,
    debug_enabled: bool,
):
    if manifest.id != "wan-character-swap":
        return

    output_node_id = str(manifest.output.node_id or "")
    output_node = prompt_graph.get(output_node_id)
    if not output_node or output_node.get("class_type") != "VHS_VideoCombine":
        raise HTTPException(500, "Wan Character Swap output node 30 is missing or invalid.")

    output_inputs = output_node.setdefault("inputs", {})
    output_inputs["images"] = ["28", 0]

    if debug_enabled:
        return

    image_source = output_inputs.get("images")
    source_node = None
    if isinstance(image_source, list) and image_source:
        source_node = prompt_graph.get(str(image_source[0]))
    if source_node and source_node.get("class_type") == "ImageConcatMulti":
        raise HTTPException(
            500,
            "Production Character Swap export cannot point VHS_VideoCombine.images at an ImageConcatMulti node.",
        )


def _apply_managed_template_patches(
    prompt_graph: dict[str, Any],
    manifest: ComfyUITemplateManifest,
    *,
    params: dict[str, Any],
    job_id: Optional[str],
):
    debug_enabled = _comfyui_debug_dump_enabled(params)
    if manifest.id == "wan-character-swap":
        _apply_wan_character_swap_targeting_overrides(prompt_graph, params)
        _configure_wan_character_swap_debug_outputs(
            prompt_graph,
            debug_enabled=debug_enabled,
            job_id=job_id,
        )
        _enforce_production_video_output(
            prompt_graph,
            manifest,
            debug_enabled=debug_enabled,
        )


def validate_managed_template_params(
    manifest: ComfyUITemplateManifest,
    params: dict[str, Any],
):
    if manifest.id != "wan-character-swap":
        return
    positive_points = params.get(WAN_CHARACTER_SWAP_POSITIVE_POINTS_PARAM)
    negative_points = params.get(WAN_CHARACTER_SWAP_NEGATIVE_POINTS_PARAM)
    if WAN_CHARACTER_SWAP_POSITIVE_POINTS_PARAM in params and positive_points is not None and positive_points != "":
        _parse_points_json(
            positive_points,
            field_name=WAN_CHARACTER_SWAP_POSITIVE_POINTS_PARAM,
        )
    if WAN_CHARACTER_SWAP_NEGATIVE_POINTS_PARAM in params and negative_points is not None and negative_points != "":
        _parse_points_json(
            negative_points,
            field_name=WAN_CHARACTER_SWAP_NEGATIVE_POINTS_PARAM,
        )


def patch_api_workflow(
    prompt_graph: dict[str, Any],
    manifest: ComfyUITemplateManifest,
    *,
    input_values: dict[str, Any],
    params: dict[str, Any],
    job_id: Optional[str] = None,
) -> dict[str, Any]:
    patched = json.loads(json.dumps(prompt_graph))
    param_specs = {spec.id: spec for spec in manifest.optional_params}

    for mapping in manifest.runtime_mappings:
        node = patched.get(str(mapping.node_id))
        if not node or "inputs" not in node:
            raise HTTPException(
                500,
                f"Template {manifest.id} is missing node {mapping.node_id} for runtime patching",
            )

        if mapping.value_source == "asset_filename":
            value = input_values.get(mapping.input_id)
        elif mapping.value_source == "literal":
            value = mapping.value
        else:
            value = _coerce_param_value(params.get(mapping.input_id), param_specs.get(mapping.input_id))

        if value is None:
            continue
        node["inputs"][mapping.input_name] = value

    _apply_managed_template_patches(
        patched,
        manifest,
        params=params,
        job_id=job_id,
    )

    return patched


def extract_debug_artifacts_from_history(
    history_entry: dict[str, Any],
    manifest: ComfyUITemplateManifest,
) -> list[dict[str, Any]]:
    if manifest.id != "wan-character-swap":
        return []

    artifacts: list[dict[str, Any]] = []
    for spec in WAN_CHARACTER_SWAP_DEBUG_OUTPUTS:
        for item in _extract_history_media_items(
            history_entry,
            node_id=spec["node_id"],
            media_keys=spec["media_keys"],
        ):
            artifacts.append(
                {
                    "id": spec["id"],
                    "label": spec["label"],
                    **item,
                }
            )
    return artifacts


def extract_output_from_history(
    prompt_graph: dict[str, Any],
    history_entry: dict[str, Any],
    manifest: ComfyUITemplateManifest,
) -> Optional[dict[str, Any]]:
    outputs = history_entry.get("outputs") or {}
    candidate_nodes: list[str] = []
    if manifest.output.node_id:
        candidate_nodes.append(str(manifest.output.node_id))
    if manifest.output.node_type:
        for node_id, node in prompt_graph.items():
            if node.get("class_type") == manifest.output.node_type:
                candidate_nodes.append(str(node_id))
    candidate_nodes.extend(outputs.keys())

    seen: set[str] = set()
    for node_id in candidate_nodes:
        if node_id in seen:
            continue
        seen.add(node_id)
        media_items = _extract_history_media_items(
            history_entry,
            node_id=str(node_id),
            media_keys=manifest.output.media_keys,
        )
        if media_items:
            return media_items[-1]
    return None


def prepare_asset_for_comfyui(asset: ComfyUIAssetRecord) -> str:
    source = resolve_asset_path(asset.relative_path, COMFYUI_UPLOADS_DIR)
    COMFYUI_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = _ensure_within_root(COMFYUI_INPUT_DIR / asset.stored_filename, COMFYUI_INPUT_DIR)
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)
    return target.name

# Both tiers serialized to 1 concurrent GPU job
gpu_heavy_sem = asyncio.Semaphore(1)
gpu_medium_sem = asyncio.Semaphore(1)
active_comfyui_tasks: dict[str, asyncio.Task] = {}

rdb: Optional[aioredis.Redis] = None
http_client: Optional[httpx.AsyncClient] = None


async def store_job(job: JobRecord):
    if rdb:
        await rdb.set(f"job:{job.job_id}", job.model_dump_json(), ex=86400)


async def get_job(job_id: str) -> Optional[JobRecord]:
    if rdb:
        data = await rdb.get(f"job:{job_id}")
        if data:
            return JobRecord.model_validate_json(data)
    with SessionLocal() as db:
        row = db.get(ComfyUIJobRecordDB, job_id)
    if row:
        params = _safe_json_loads(row.params_json, {})
        return JobRecord(
            job_id=row.id,
            service="comfyui",
            status=JobStatus(row.status),
            created_at=row.created_at.isoformat(),
            started_at=row.started_at.isoformat() if row.started_at else None,
            completed_at=row.completed_at.isoformat() if row.completed_at else None,
            result_path=row.output_path,
            debug_dump_path=_serialize_comfyui_debug_dump_path(row.id, params),
            debug_artifacts=_safe_json_loads(row.artifacts_json, []),
            message=row.status_message,
            error=row.error,
        )
    return None


async def record_service_activity(service: str):
    if rdb:
        await rdb.set(f"activity:{service}", str(time.time()), ex=7200)


def _gpu_sem_for_tier(tier: str) -> Optional[asyncio.Semaphore]:
    if tier == "heavy":
        return gpu_heavy_sem
    if tier == "medium":
        return gpu_medium_sem
    return None


async def sync_comfyui_job_to_store(record: ComfyUIJobRecordDB):
    params = _safe_json_loads(record.params_json, {})
    await store_job(
        JobRecord(
            job_id=record.id,
            service="comfyui",
            status=JobStatus(record.status),
            created_at=record.created_at.isoformat(),
            started_at=record.started_at.isoformat() if record.started_at else None,
            completed_at=record.completed_at.isoformat() if record.completed_at else None,
            result_path=record.output_path,
            debug_dump_path=_serialize_comfyui_debug_dump_path(record.id, params),
            debug_artifacts=_safe_json_loads(record.artifacts_json, []),
            message=record.status_message,
            error=record.error,
        )
    )


async def update_comfyui_job(
    job_id: str,
    **updates: Any,
) -> ComfyUIJobRecordDB:
    now = _now_utc()
    with SessionLocal() as db:
        row = db.get(ComfyUIJobRecordDB, job_id)
        if not row:
            raise HTTPException(
                404,
                _comfyui_error_detail("ComfyUI job not found.", job_id=job_id),
            )
        for key, value in updates.items():
            setattr(row, key, value)
        row.updated_at = now
        db.commit()
        db.refresh(row)
    await sync_comfyui_job_to_store(row)
    return row


def get_comfyui_asset_or_404(
    asset_id: str,
    *,
    input_id: Optional[str] = None,
    template_id: Optional[str] = None,
) -> ComfyUIAssetRecord:
    with SessionLocal() as db:
        row = db.get(ComfyUIAssetRecord, asset_id)
    if not row:
        raise HTTPException(
            404,
            _comfyui_error_detail(
                "ComfyUI asset not found.",
                asset_id=asset_id,
                input_id=input_id,
                template_id=template_id,
            ),
        )
    return row


def get_comfyui_job_or_404(job_id: str) -> ComfyUIJobRecordDB:
    with SessionLocal() as db:
        row = db.get(ComfyUIJobRecordDB, job_id)
    if not row:
        raise HTTPException(
            404,
            _comfyui_error_detail("ComfyUI job not found.", job_id=job_id),
        )
    return row


def resolve_comfyui_input_assets(
    manifest: ComfyUITemplateManifest,
    input_asset_ids: dict[str, Any],
) -> dict[str, ComfyUIAssetRecord]:
    resolved_assets: dict[str, ComfyUIAssetRecord] = {}
    for spec in manifest.required_inputs:
        asset_id = input_asset_ids.get(spec.id)
        if not asset_id:
            raise HTTPException(
                400,
                _comfyui_error_detail(
                    "Missing required ComfyUI input asset.",
                    template_id=manifest.id,
                    input_id=spec.id,
                    expected_kind=spec.kind,
                ),
            )
        asset = get_comfyui_asset_or_404(
            str(asset_id),
            input_id=spec.id,
            template_id=manifest.id,
        )
        if asset.kind != spec.kind:
            raise HTTPException(
                400,
                _comfyui_error_detail(
                    "Selected asset kind does not match the template input.",
                    template_id=manifest.id,
                    input_id=spec.id,
                    asset_id=asset.id,
                    expected_kind=spec.kind,
                    actual_kind=asset.kind,
                ),
            )
        resolved_assets[spec.id] = asset
    return resolved_assets


async def save_comfyui_upload(upload: UploadFile, kind: str) -> ComfyUIAssetRecord:
    limit_bytes = comfyui_asset_size_limit_bytes(kind)
    kind_dir = COMFYUI_UPLOADS_DIR / kind
    kind_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_filename(upload.filename or f"{kind}.bin")
    stored_filename = f"{uuid.uuid4().hex}_{safe_name}"
    relative_path = str(Path(kind) / stored_filename)
    target_path = _ensure_within_root(COMFYUI_UPLOADS_DIR / relative_path, COMFYUI_UPLOADS_DIR)

    size_bytes = 0
    try:
        with target_path.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > limit_bytes:
                    handle.close()
                    target_path.unlink(missing_ok=True)
                    raise HTTPException(
                        400,
                        f"{kind.title()} file exceeds the {limit_bytes // (1024 * 1024)} MB limit.",
                    )
                handle.write(chunk)
    finally:
        await upload.close()

    record = ComfyUIAssetRecord(
        id=str(uuid.uuid4()),
        kind=kind,
        original_filename=upload.filename or safe_name,
        stored_filename=stored_filename,
        relative_path=relative_path,
        content_type=upload.content_type,
        size_bytes=size_bytes,
        created_at=_now_utc(),
    )
    with SessionLocal() as db:
        db.add(record)
        db.commit()
        db.refresh(record)
    return record


async def ensure_comfyui_reachable():
    try:
        response = await http_client.get(f"{COMFYUI_URL.rstrip('/')}/", timeout=5.0)
        if response.status_code >= 500:
            raise HTTPException(503, "ComfyUI is unavailable right now.")
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise HTTPException(503, f"ComfyUI is unreachable at {COMFYUI_URL}: {exc}") from exc


async def convert_template_workflow_to_api(manifest: ComfyUITemplateManifest) -> dict[str, Any]:
    workflow = load_template_workflow(manifest)
    if workflow_is_api_format(workflow):
        return json.loads(json.dumps(workflow))

    workflow_path = resolve_template_workflow_path(manifest)
    cache_key = (str(workflow_path), _workflow_file_mtime(workflow_path))
    cached = _template_api_cache.get(cache_key)
    if cached is not None:
        return json.loads(json.dumps(cached))

    try:
        response = await http_client.post(
            f"{COMFYUI_URL.rstrip('/')}/workflow/convert",
            json=workflow,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        if response.status_code == 404:
            raise HTTPException(
                503,
                "ComfyUI workflow conversion endpoint is unavailable. "
                "Install the workflow-to-API converter custom node in the ComfyUI container.",
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Failed to convert workflow template via ComfyUI: {exc}") from exc

    api_prompt = response.json()
    if not workflow_is_api_format(api_prompt):
        raise HTTPException(502, "ComfyUI returned an invalid API workflow conversion response.")

    _template_api_cache[cache_key] = api_prompt
    return json.loads(json.dumps(api_prompt))


def _queue_entry_contains_prompt(entry: Any, prompt_id: str) -> bool:
    if isinstance(entry, dict):
        if entry.get("prompt_id") == prompt_id:
            return True
        return any(_queue_entry_contains_prompt(value, prompt_id) for value in entry.values())
    if isinstance(entry, list):
        return any(_queue_entry_contains_prompt(value, prompt_id) for value in entry)
    return entry == prompt_id


async def get_comfyui_queue_status(prompt_id: str) -> Optional[str]:
    try:
        response = await http_client.get(f"{COMFYUI_URL.rstrip('/')}/queue", timeout=10.0)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return None

    running_entries = payload.get("queue_running") or payload.get("running") or []
    pending_entries = payload.get("queue_pending") or payload.get("pending") or []

    if _queue_entry_contains_prompt(running_entries, prompt_id):
        return "running"
    if _queue_entry_contains_prompt(pending_entries, prompt_id):
        return "queued"
    return None


async def get_comfyui_history_entry(prompt_id: str) -> Optional[dict[str, Any]]:
    try:
        response = await http_client.get(
            f"{COMFYUI_URL.rstrip('/')}/history/{prompt_id}",
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        return None

    if isinstance(payload, dict):
        if prompt_id in payload and isinstance(payload[prompt_id], dict):
            return payload[prompt_id]
        if "outputs" in payload:
            return payload
    return None


def _history_status_str(history_entry: dict[str, Any]) -> str:
    status = history_entry.get("status")
    if isinstance(status, dict):
        for key in ("status_str", "status"):
            value = status.get(key)
            if isinstance(value, str):
                return value.lower()
    return ""


def _history_error_message(history_entry: dict[str, Any]) -> Optional[str]:
    status = history_entry.get("status")
    if isinstance(status, dict):
        for key in ("error", "message"):
            value = status.get(key)
            if isinstance(value, str) and value.strip():
                return value
        messages = status.get("messages")
        if isinstance(messages, list):
            for item in reversed(messages):
                if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict):
                    message = item[1].get("exception_message") or item[1].get("message")
                    if isinstance(message, str) and message.strip():
                        return message
    return None


def _register_comfyui_task(job_id: str, task: asyncio.Task):
    active_comfyui_tasks[job_id] = task

    def _cleanup(completed_task: asyncio.Task):
        active_comfyui_tasks.pop(job_id, None)
        try:
            completed_task.result()
        except Exception:
            log.exception("Background ComfyUI task failed for job %s", job_id)

    task.add_done_callback(_cleanup)


async def run_comfyui_job(job_id: str):
    job_row = get_comfyui_job_or_404(job_id)
    manifest = get_comfyui_template(job_row.template_id)
    semaphore = _gpu_sem_for_tier(job_row.gpu_tier)

    async def _execute():
        current_job = job_row
        validation = validate_template_models(manifest)
        if validation["missing"]:
            missing_names = ", ".join(sorted({item["filename"] for item in validation["missing"]}))
            await update_comfyui_job(
                job_id,
                status=JobStatus.FAILED.value,
                validation_json=json.dumps(validation, ensure_ascii=False),
                error=f"Missing required ComfyUI models: {missing_names}",
                status_message="Missing model files",
                completed_at=_now_utc(),
            )
            return

        allowed, mem, reason = memory_allows_job(current_job.gpu_tier)
        if not allowed and current_job.gpu_tier != "light":
            await update_comfyui_job(
                job_id,
                status=JobStatus.FAILED.value,
                validation_json=json.dumps(validation, ensure_ascii=False),
                error=reason,
                status_message=f"Blocked by UMA memory gate ({mem['used_pct']}%)",
                completed_at=_now_utc(),
            )
            return

        await ensure_comfyui_reachable()

        input_asset_ids = _safe_json_loads(current_job.inputs_json, {})
        params = _safe_json_loads(current_job.params_json, {})
        public_params = _public_comfyui_params(params)
        prepared_inputs: dict[str, Any] = {}
        resolved_assets = resolve_comfyui_input_assets(manifest, input_asset_ids)
        for spec in manifest.required_inputs:
            asset = resolved_assets[spec.id]
            prepared_inputs[spec.id] = prepare_asset_for_comfyui(asset)

        api_prompt = await convert_template_workflow_to_api(manifest)
        patched_prompt = patch_api_workflow(
            api_prompt,
            manifest,
            input_values=prepared_inputs,
            params=params,
            job_id=job_id,
        )
        prompt_json = _serialize_prompt_graph_for_submission(patched_prompt)
        prompt_sha256 = hashlib.sha256(prompt_json.encode("utf-8")).hexdigest()
        debug_dump_path: Optional[str] = None
        if _comfyui_debug_dump_enabled(params):
            dump_path = _comfyui_debug_dump_path(job_id)
            try:
                dump_path.parent.mkdir(parents=True, exist_ok=True)
                dump_path.write_text(prompt_json, encoding="utf-8")
                debug_dump_path = str(dump_path)
            except OSError as exc:
                raise HTTPException(500, f"Failed to write ComfyUI debug dump: {exc}") from exc

        if manifest.id == "wan-character-swap":
            _log_wan_character_swap_prompt_diagnostics(
                patched_prompt,
                base_prompt_graph=api_prompt,
                job_id=job_id,
                prompt_sha256=prompt_sha256,
                debug_dump_path=debug_dump_path,
            )

        client_id = current_job.client_id or str(uuid.uuid4())
        current_job = await update_comfyui_job(
            job_id,
            client_id=client_id,
            validation_json=json.dumps(validation, ensure_ascii=False),
            status_message="Submitting to ComfyUI",
        )
        await record_service_activity("comfyui")
        submission_body = _build_comfyui_prompt_request_body(
            client_id=client_id,
            prompt_json=prompt_json,
            job_id=job_id,
            manifest=manifest,
        )

        response = await http_client.post(
            f"{COMFYUI_URL.rstrip('/')}/prompt",
            content=submission_body.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        response.raise_for_status()
        queue_response = response.json()

        prompt_id = queue_response.get("prompt_id")
        node_errors = queue_response.get("node_errors")
        if node_errors:
            raise HTTPException(400, f"ComfyUI rejected the workflow: {json.dumps(node_errors, ensure_ascii=False)}")
        if not prompt_id:
            raise HTTPException(502, "ComfyUI accepted the request but did not return a prompt_id.")

        await update_comfyui_job(
            job_id,
            prompt_id=prompt_id,
            status=JobStatus.QUEUED.value,
            started_at=_now_utc(),
            status_message="Queued in ComfyUI",
        )

        started_monotonic = time.monotonic()
        while True:
            if time.monotonic() - started_monotonic > COMFYUI_JOB_TIMEOUT_SEC:
                raise HTTPException(504, "ComfyUI job timed out while waiting for completion.")

            history_entry = await get_comfyui_history_entry(prompt_id)
            if history_entry:
                output = extract_output_from_history(patched_prompt, history_entry, manifest)
                if output:
                    debug_artifacts = extract_debug_artifacts_from_history(history_entry, manifest)
                    history_payload = {
                        "template_id": manifest.id,
                        "template_name": manifest.name,
                        "inputs": prepared_inputs,
                        "params": public_params,
                        "prompt_id": prompt_id,
                    }
                    history_id = persist_generation_record(
                        job_id=job_id,
                        service="comfyui",
                        payload=history_payload,
                        output_path=output["relative_path"],
                        model_used=manifest.name,
                    )
                    await update_comfyui_job(
                        job_id,
                        status=JobStatus.COMPLETED.value,
                        output_path=output["relative_path"],
                        output_node_id=output["node_id"],
                        history_id=history_id,
                        artifacts_json=json.dumps(debug_artifacts, ensure_ascii=False),
                        completed_at=_now_utc(),
                        status_message="Completed",
                        error=None,
                    )
                    return

                status_str = _history_status_str(history_entry)
                if status_str in {"error", "failed"}:
                    raise HTTPException(502, _history_error_message(history_entry) or "ComfyUI job failed.")

            queue_status = await get_comfyui_queue_status(prompt_id)
            if queue_status == "running" and current_job.status != JobStatus.RUNNING.value:
                current_job = await update_comfyui_job(
                    job_id,
                    status=JobStatus.RUNNING.value,
                    status_message="Generating video",
                )
            elif queue_status == "queued" and current_job.status != JobStatus.QUEUED.value:
                current_job = await update_comfyui_job(
                    job_id,
                    status=JobStatus.QUEUED.value,
                    status_message="Queued in ComfyUI",
                )

            await record_service_activity("comfyui")
            await asyncio.sleep(COMFYUI_POLL_INTERVAL_SEC)

    try:
        if semaphore:
            async with semaphore:
                await _execute()
        else:
            await _execute()
    except HTTPException as exc:
        try:
            await update_comfyui_job(
                job_id,
                status=JobStatus.FAILED.value,
                error=_http_error_text(exc.detail),
                status_message="Failed",
                completed_at=_now_utc(),
            )
        except HTTPException as update_exc:
            log.warning(
                "Unable to mark ComfyUI job %s as failed after HTTP error: %s",
                job_id,
                _http_error_text(update_exc.detail),
            )
    except Exception as exc:
        try:
            await update_comfyui_job(
                job_id,
                status=JobStatus.FAILED.value,
                error=str(exc),
                status_message="Failed",
                completed_at=_now_utc(),
            )
        except HTTPException as update_exc:
            log.warning(
                "Unable to mark ComfyUI job %s as failed after exception: %s",
                job_id,
                _http_error_text(update_exc.detail),
            )


async def resume_incomplete_comfyui_jobs():
    with SessionLocal() as db:
        rows = db.execute(
            select(ComfyUIJobRecordDB).where(
                ComfyUIJobRecordDB.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value])
            )
        ).scalars().all()

    for row in rows:
        if row.id in active_comfyui_tasks:
            continue
        task = asyncio.create_task(run_comfyui_job(row.id))
        _register_comfyui_task(row.id, task)


# ---------------------------------------------------------------------------
# Supervisor delegation (NO Docker socket in this container)
# ---------------------------------------------------------------------------

async def ensure_service_running(service: str) -> bool:
    """Ask the supervisor sidecar to start a lazy service."""
    url = SERVICE_URLS.get(service)
    if not url:
        return False

    try:
        resp = await http_client.get(f"{url}/healthz", timeout=3.0)
        if resp.status_code == 200:
            return True
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    log.info("Requesting supervisor to start: %s", service)
    try:
        resp = await http_client.post(
            f"{SUPERVISOR_URL}/start/{service}",
            params={"wait_ready": "true"},
            timeout=httpx.Timeout(360.0, connect=10.0),
        )
        if resp.status_code == 200:
            data = resp.json()
            log.info("Supervisor response for %s: %s", service, data)
            return data.get("ready", False) or data.get("alive", False)

        log.error("Supervisor failed to start %s: HTTP %d", service, resp.status_code)
        return False
    except Exception as e:
        log.error("Supervisor communication failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global rdb, http_client

    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMFYUI_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    COMFYUI_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=db_engine)
    run_sqlite_schema_migrations()

    rdb = aioredis.from_url(REDIS_URL, decode_responses=True)
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    await resume_incomplete_comfyui_jobs()

    log.info(
        "Gateway v3 started (history_db=%s, comfyui_templates=%s, hard_limit=%d%%, "
        "heavy_reserve=%.0fGB, medium_reserve=%.0fGB, gpu_concurrency=1/1)",
        HISTORY_DB_PATH,
        COMFYUI_TEMPLATE_DIR,
        MEMORY_HARD_PCT,
        MEM_RESERVE_HEAVY_GB,
        MEM_RESERVE_MEDIUM_GB,
    )
    yield

    if http_client:
        await http_client.aclose()
    if rdb:
        await rdb.aclose()


app = FastAPI(title="DGX Spark AI Gateway", version="3.0.0", lifespan=lifespan)

# CORS — allow frontend to call gateway directly if not using the Next.js proxy
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    return {"status": "alive", "timestamp": _now_utc().isoformat()}


@app.get("/readyz")
async def readyz():
    if not rdb:
        return {"status": "degraded", "redis": False}

    try:
        await rdb.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ready" if redis_ok else "degraded", "redis": redis_ok}


@app.get("/memory")
async def memory():
    return get_memory_status()


@app.get("/services/status")
async def services_status():
    results = {}
    for name, url in SERVICE_URLS.items():
        try:
            resp = await http_client.get(f"{url}/healthz", timeout=3.0)
            alive = resp.status_code == 200
        except Exception:
            alive = False

        ready = False
        if alive:
            try:
                resp = await http_client.get(f"{url}/readyz", timeout=3.0)
                ready = resp.status_code == 200
            except Exception:
                pass

        last_activity = None
        if rdb:
            ts = await rdb.get(f"activity:{name}")
            if ts:
                last_activity = float(ts)

        results[name] = {"alive": alive, "ready": ready, "last_activity": last_activity}
    return results


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

async def proxy_to_service(
    service: str,
    path: str,
    request: Request,
    files: dict | None = None,
    data: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    is_gpu_heavy: bool = False,
    is_gpu_medium: bool = False,
    public_service: str | None = None,
    history_model_used: str | None = None,
):
    job_service = public_service or service
    job_id = str(uuid.uuid4())
    now = _now_utc().isoformat()
    job = JobRecord(job_id=job_id, service=job_service, status=JobStatus.QUEUED, created_at=now)
    await store_job(job)

    tier = "heavy" if is_gpu_heavy else "medium" if is_gpu_medium else "light"
    allowed, mem, reason = memory_allows_job(tier)
    if not allowed and tier != "light":
        job.status = JobStatus.FAILED
        job.error = reason
        await store_job(job)
        raise HTTPException(503, detail={"error": reason, "memory": mem, "job_id": job_id})

    if service in GPU_HEAVY_SERVICES:
        ready = await ensure_service_running(service)
        if not ready:
            job.status = JobStatus.FAILED
            job.error = "Service failed to start via supervisor"
            await store_job(job)
            raise HTTPException(503, f"Service {service} failed to start")

    sem = gpu_heavy_sem if is_gpu_heavy else gpu_medium_sem if is_gpu_medium else None
    url = f"{SERVICE_URLS[service]}{path}"

    payload_for_history: dict[str, Any] = {}
    if json_body is not None:
        payload_for_history = dict(json_body)
    elif data:
        payload_for_history = dict(data)

    if files:
        payload_for_history.update(
            {
                f"{field}_filename": file_tuple[0]
                for field, file_tuple in files.items()
                if isinstance(file_tuple, tuple) and len(file_tuple) >= 1
            }
        )

    async def do_request():
        nonlocal payload_for_history

        job.status = JobStatus.RUNNING
        job.started_at = _now_utc().isoformat()
        await store_job(job)
        await record_service_activity(job_service)

        try:
            if files:
                resp = await http_client.post(url, files=files, data=data or {})
            elif json_body is not None:
                resp = await http_client.post(url, json=json_body)
            else:
                body = await request.body()
                content_type = request.headers.get("content-type", "application/json")
                if not payload_for_history and "application/json" in content_type and body:
                    try:
                        payload_for_history = json.loads(body.decode("utf-8"))
                    except json.JSONDecodeError:
                        payload_for_history = {}
                resp = await http_client.post(
                    url,
                    content=body,
                    headers={"content-type": content_type},
                )

            resp.raise_for_status()
            result = resp.json()

            job.status = JobStatus.COMPLETED
            job.completed_at = _now_utc().isoformat()
            job.result_path = result.get("output_path")
            await store_job(job)

            if job.result_path:
                try:
                    persist_generation_record(
                        job_id=job_id,
                        service=job_service,
                        payload=payload_for_history,
                        output_path=job.result_path,
                        model_used=history_model_used,
                    )
                except Exception as hist_error:
                    log.warning("History persistence failed for job %s: %s", job_id, hist_error)

            result["job_id"] = job_id
            return result
        except httpx.HTTPStatusError as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            await store_job(job)
            raise HTTPException(e.response.status_code, str(e))
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            await store_job(job)
            raise HTTPException(502, f"Backend error: {e}")

    if sem:
        async with sem:
            return await do_request()
    return await do_request()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/whisper/transcribe")
async def whisper_transcribe(request: Request, audio: UploadFile = File(...)):
    content = await audio.read()
    files = {"audio": (audio.filename, content, audio.content_type)}
    return await proxy_to_service("whisper", "/transcribe", request, files=files)


@app.get("/api/v1/tts/providers")
async def list_tts_providers(include_disabled: bool = True):
    return {
        "items": TTS_PROVIDER_REGISTRY.list(include_disabled=include_disabled),
    }


@app.get("/api/v1/tts/providers/{provider_id}/health")
async def get_tts_provider_health(provider_id: str):
    provider = TTS_PROVIDER_REGISTRY.get(provider_id)
    return await provider.health_check()


@app.post("/api/v1/tts/jobs")
async def create_tts_job(request: Request):
    return await create_tts_job_from_request(request)


@app.get("/api/v1/tts/jobs/{job_id}")
async def get_tts_job(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/v1/tts/history")
async def list_tts_history(limit: int = 200, provider: Optional[str] = None):
    clamped_limit = max(1, min(limit, 1000))
    allowed_services = TTS_PROVIDER_IDS
    if provider:
        if provider not in allowed_services:
            raise HTTPException(404, f"Unknown TTS provider '{provider}'.")
        allowed_services = {provider}

    with SessionLocal() as db:
        stmt = (
            select(GenerationHistory)
            .where(GenerationHistory.service.in_(sorted(allowed_services)))
            .order_by(desc(GenerationHistory.created_at))
            .limit(clamped_limit)
        )
        rows = db.execute(stmt).scalars().all()

    return {"items": [serialize_generation(row) for row in rows]}


@app.post("/api/v1/tts/synthesize")
async def tts_synthesize(request: Request):
    return await create_tts_job_from_request(request, default_provider="f5tts")


@app.post("/api/v1/tts/synthesize-with-audio")
async def tts_synthesize_with_audio(request: Request):
    return await create_tts_job_from_request(request, default_provider="f5tts")

@app.post("/api/v1/liveportrait/animate")
async def liveportrait_animate(
    request: Request,
    source_image: UploadFile = File(...),
    driving_video: UploadFile = File(...),
):
    src_data = await source_image.read()
    drv_data = await driving_video.read()
    files = {
        "source_image": (source_image.filename, src_data, source_image.content_type),
        "driving_video": (driving_video.filename, drv_data, driving_video.content_type),
    }
    return await proxy_to_service("liveportrait", "/animate", request, files=files, is_gpu_medium=True)


@app.post("/api/v1/lipsync/sync")
async def lipsync_sync(request: Request, video: UploadFile = File(...), audio: UploadFile = File(...)):
    vid_data = await video.read()
    aud_data = await audio.read()
    files = {
        "video": (video.filename, vid_data, video.content_type),
        "audio": (audio.filename, aud_data, audio.content_type),
    }
    return await proxy_to_service("lipsync", "/sync", request, files=files, is_gpu_medium=True)


@app.post("/api/v1/wan21/generate")
async def wan21_generate(request: Request):
    return await proxy_to_service("wan21", "/generate", request, is_gpu_heavy=True)


@app.post("/api/v1/reactor/generate")
async def reactor_generate(req: ReactorGenerateRequest):
    """
    Boilerplate ReActor endpoint for ComfyUI integration.

    This queues a prompt to ComfyUI's `/prompt` API while carrying the selected
    LoRA path in payload metadata so custom workflow nodes can consume it.
    """
    if not req.prompt.strip():
        raise HTTPException(400, "prompt is required")

    lora_path = None
    if req.lora_path:
        lora_path = str(resolve_asset_path(req.lora_path, LORA_ASSETS_DIR))

    payload_snapshot = {
        "prompt": req.prompt,
        "negative_prompt": req.negative_prompt,
        "lora_path": lora_path,
        "lora_strength": req.lora_strength,
        **req.parameters,
    }

    comfy_payload = {
        "client_id": str(uuid.uuid4()),
        "extra_data": {
            "neonforge": payload_snapshot,
        },
        "prompt": req.workflow
        or {
            "neonforge_reactor": {
                "class_type": "NeonForgeReActorInput",
                "inputs": payload_snapshot,
            }
        },
    }

    job_id = str(uuid.uuid4())
    job = JobRecord(
        job_id=job_id,
        service="reactor",
        status=JobStatus.QUEUED,
        created_at=_now_utc().isoformat(),
    )
    await store_job(job)

    allowed, mem, reason = memory_allows_job("medium")
    if not allowed:
        job.status = JobStatus.FAILED
        job.error = reason
        await store_job(job)
        raise HTTPException(503, detail={"error": reason, "memory": mem, "job_id": job_id})

    async with gpu_medium_sem:
        job.status = JobStatus.RUNNING
        job.started_at = _now_utc().isoformat()
        await store_job(job)
        await record_service_activity("reactor")

        try:
            resp = await http_client.post(
                f"{COMFYUI_URL.rstrip('/')}/prompt",
                json=comfy_payload,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
            resp.raise_for_status()
            data = resp.json()

            output_path = data.get("output_path") if isinstance(data, dict) else None
            job.status = JobStatus.COMPLETED
            job.completed_at = _now_utc().isoformat()
            job.result_path = output_path
            await store_job(job)

            if output_path:
                try:
                    persist_generation_record(
                        job_id=job_id,
                        service="reactor",
                        payload=payload_snapshot,
                        output_path=output_path,
                        model_used=MODEL_NAME_BY_SERVICE["reactor"],
                    )
                except Exception as hist_error:
                    log.warning("History persistence failed for reactor job %s: %s", job_id, hist_error)

            return {
                "job_id": job_id,
                "output_path": output_path,
                "queue_response": data,
            }
        except httpx.HTTPStatusError as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            await store_job(job)
            raise HTTPException(e.response.status_code, str(e))
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            await store_job(job)
            raise HTTPException(502, f"ComfyUI proxy error: {e}")


@app.get("/api/v1/comfyui/templates")
async def list_comfyui_templates():
    items = []
    for manifest in load_comfyui_templates():
        items.append(
            {
                **serialize_comfyui_template(manifest),
                "validation": validate_template_models(manifest),
            }
        )
    return {"items": items}


@app.get("/api/v1/comfyui/templates/{template_id}")
async def get_comfyui_template_detail(template_id: str):
    manifest = get_comfyui_template(template_id)
    return {
        **serialize_comfyui_template(manifest, include_workflow_file=True),
        "validation": validate_template_models(manifest),
    }


@app.get("/api/v1/comfyui/models")
async def list_comfyui_models():
    inventory = scan_comfyui_models()
    return {
        "roots": inventory["roots"],
        "scanned_roots": inventory["scanned_roots"],
        "items": inventory["items"],
        "templates": [
            {
                "template_id": manifest.id,
                "template_name": manifest.name,
                "validation": validate_template_models(manifest),
            }
            for manifest in load_comfyui_templates()
        ],
    }


@app.get("/api/v1/comfyui/assets")
async def list_comfyui_assets(kind: Optional[str] = None):
    with SessionLocal() as db:
        stmt = select(ComfyUIAssetRecord).order_by(desc(ComfyUIAssetRecord.created_at))
        if kind:
            stmt = stmt.where(ComfyUIAssetRecord.kind == kind)
        rows = db.execute(stmt).scalars().all()
    return {
        "root": str(COMFYUI_UPLOADS_DIR),
        "items": [serialize_comfyui_asset(row) for row in rows],
    }


@app.post("/api/v1/comfyui/assets/upload")
async def upload_comfyui_asset(file: UploadFile = File(...), kind: str = Form("")):
    detected_kind = kind.strip().lower() or guess_comfyui_asset_kind(file.filename or "", file.content_type)
    if detected_kind not in {"image", "video"}:
        raise HTTPException(400, "Only image and video uploads are supported.")
    suffix = Path(file.filename or "").suffix.lower()
    if detected_kind == "image" and suffix not in COMFYUI_IMAGE_EXTENSIONS:
        raise HTTPException(400, "Unsupported image format.")
    if detected_kind == "video" and suffix not in COMFYUI_VIDEO_EXTENSIONS:
        raise HTTPException(400, "Unsupported video format.")
    record = await save_comfyui_upload(file, detected_kind)
    return serialize_comfyui_asset(record)


@app.delete("/api/v1/comfyui/assets/{asset_id}")
async def delete_comfyui_asset(asset_id: str):
    with SessionLocal() as db:
        row = db.get(ComfyUIAssetRecord, asset_id)
        if not row:
            raise HTTPException(404, "Asset not found")

        file_deleted = False
        try:
            path = resolve_asset_path(row.relative_path, COMFYUI_UPLOADS_DIR)
            if path.exists():
                path.unlink()
                file_deleted = True
        except HTTPException:
            pass

        db.delete(row)
        db.commit()

    return {"deleted": True, "file_deleted": file_deleted}


@app.post("/api/v1/comfyui/jobs")
async def create_comfyui_job(payload: ComfyUIJobCreateRequest):
    manifest = get_comfyui_template(payload.template_id)
    validation = validate_template_models(manifest)
    if validation["missing"]:
        missing_names = ", ".join(sorted({item["filename"] for item in validation["missing"]}))
        raise HTTPException(400, {"error": f"Missing required models: {missing_names}", "validation": validation})

    required_ids = {spec.id for spec in manifest.required_inputs}
    missing_inputs = sorted(input_id for input_id in required_ids if not payload.inputs.get(input_id))
    if missing_inputs:
        raise HTTPException(
            400,
            _comfyui_error_detail(
                "Missing required template inputs.",
                template_id=manifest.id,
                missing_inputs=missing_inputs,
            ),
        )

    resolve_comfyui_input_assets(manifest, payload.inputs)
    validate_managed_template_params(manifest, payload.params)

    stored_params = dict(payload.params)
    if payload.debug_dump:
        stored_params[COMFYUI_INTERNAL_DEBUG_PARAM] = True

    job = ComfyUIJobRecordDB(
        id=str(uuid.uuid4()),
        template_id=manifest.id,
        template_name=manifest.name,
        gpu_tier=manifest.gpu_tier,
        client_id=str(uuid.uuid4()),
        prompt_id=None,
        status=JobStatus.QUEUED.value,
        inputs_json=json.dumps(payload.inputs, ensure_ascii=False),
        params_json=json.dumps(stored_params, ensure_ascii=False),
        validation_json=json.dumps(validation, ensure_ascii=False),
        artifacts_json=None,
        output_path=None,
        output_node_id=None,
        history_id=None,
        status_message="Queued in gateway",
        error=None,
        created_at=_now_utc(),
        started_at=None,
        completed_at=None,
        updated_at=_now_utc(),
    )
    with SessionLocal() as db:
        db.add(job)
        db.commit()
        db.refresh(job)

    await sync_comfyui_job_to_store(job)
    task = asyncio.create_task(run_comfyui_job(job.id))
    _register_comfyui_task(job.id, task)
    return {
        "job_id": job.id,
        "status": job.status,
        "template_id": manifest.id,
        "validation": validation,
        "debug_dump_path": str(_comfyui_debug_dump_path(job.id)) if payload.debug_dump else None,
    }


@app.get("/api/v1/comfyui/jobs/{job_id}")
async def get_comfyui_job(job_id: str):
    row = get_comfyui_job_or_404(job_id)
    return serialize_comfyui_job_record(row)


@app.get("/api/v1/outputs/{filepath:path}")
async def get_output(filepath: str):
    full = resolve_output_path(filepath)
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(
        full,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


# ---------------------------------------------------------------------------
# History APIs
# ---------------------------------------------------------------------------

@app.get("/api/v1/history")
async def list_history(limit: int = 200, service: Optional[str] = None):
    clamped_limit = max(1, min(limit, 1000))
    with SessionLocal() as db:
        stmt = select(GenerationHistory)
        if service:
            stmt = stmt.where(GenerationHistory.service == service)
        stmt = stmt.order_by(desc(GenerationHistory.created_at)).limit(clamped_limit)
        rows = db.execute(stmt).scalars().all()

    return {"items": [serialize_generation(row) for row in rows]}


@app.get("/api/v1/history/{history_id}/download")
async def download_history_file(history_id: str):
    with SessionLocal() as db:
        row = db.get(GenerationHistory, history_id)

    if not row:
        raise HTTPException(404, "History item not found")

    full = resolve_output_path(row.output_path)
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "Output file is missing")

    return FileResponse(full, filename=full.name, media_type="application/octet-stream")


@app.delete("/api/v1/history/{history_id}")
async def delete_history_item(history_id: str):
    with SessionLocal() as db:
        row = db.get(GenerationHistory, history_id)
        if not row:
            raise HTTPException(404, "History item not found")

        file_deleted = False
        try:
            full = resolve_output_path(row.output_path)
            if full.exists() and full.is_file():
                full.unlink()
                file_deleted = True
        except HTTPException:
            # Path is invalid/outside root; still delete DB record.
            pass

        db.delete(row)
        db.commit()

    return {"deleted": True, "file_deleted": file_deleted}


# ---------------------------------------------------------------------------
# Asset APIs
# ---------------------------------------------------------------------------

@app.get("/api/v1/assets/voices")
async def list_voice_assets():
    return {
        "root": str(VOICE_ASSETS_DIR),
        "items": list_asset_files(VOICE_ASSETS_DIR, VOICE_EXTENSIONS),
    }


@app.get("/api/v1/assets/loras")
async def list_lora_assets():
    return {
        "root": str(LORA_ASSETS_DIR),
        "items": list_asset_files(LORA_ASSETS_DIR, LORA_EXTENSIONS),
    }


# ---------------------------------------------------------------------------
# Preset APIs
# ---------------------------------------------------------------------------

@app.get("/api/v1/presets")
async def list_presets(tool: Optional[str] = None, limit: int = 200):
    clamped_limit = max(1, min(limit, 1000))

    with SessionLocal() as db:
        stmt = select(PresetProfile)
        if tool:
            stmt = stmt.where(PresetProfile.tool == tool)
        stmt = stmt.order_by(desc(PresetProfile.updated_at)).limit(clamped_limit)
        rows = db.execute(stmt).scalars().all()

    return {"items": [serialize_preset(row) for row in rows]}


@app.get("/api/v1/presets/{preset_id}")
async def get_preset(preset_id: str):
    with SessionLocal() as db:
        row = db.get(PresetProfile, preset_id)

    if not row:
        raise HTTPException(404, "Preset not found")

    return serialize_preset(row)


@app.post("/api/v1/presets")
async def upsert_preset(payload: PresetUpsertRequest):
    now = _now_utc()

    with SessionLocal() as db:
        stmt = select(PresetProfile).where(
            PresetProfile.name == payload.name,
            PresetProfile.tool == payload.tool,
        )
        existing = db.execute(stmt).scalar_one_or_none()

        if existing:
            existing.state_json = json.dumps(payload.state, ensure_ascii=False)
            existing.updated_at = now
            db.commit()
            db.refresh(existing)
            return serialize_preset(existing)

        record = PresetProfile(
            id=str(uuid.uuid4()),
            name=payload.name,
            tool=payload.tool,
            state_json=json.dumps(payload.state, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return serialize_preset(record)


@app.delete("/api/v1/presets/{preset_id}")
async def delete_preset(preset_id: str):
    with SessionLocal() as db:
        row = db.get(PresetProfile, preset_id)
        if not row:
            raise HTTPException(404, "Preset not found")

        db.delete(row)
        db.commit()

    return {"deleted": True}
