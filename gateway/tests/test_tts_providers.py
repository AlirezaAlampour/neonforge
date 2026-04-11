import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as gateway_app


@asynccontextmanager
async def _test_lifespan(app):
    gateway_app.rdb = None
    gateway_app.http_client = None
    yield


def test_list_tts_providers_includes_capabilities(monkeypatch):
    monkeypatch.setattr(gateway_app.app.router, "lifespan_context", _test_lifespan)

    with TestClient(gateway_app.app) as client:
        response = client.get("/api/v1/tts/providers")

    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload

    providers = {item["provider_id"]: item for item in payload["items"]}
    assert "f5tts" in providers
    assert providers["f5tts"]["display_name"] == "F5-TTS"
    assert providers["f5tts"]["capabilities"]["voice_clone"] is True
    assert providers["f5tts"]["capabilities"]["supports_reference_audio"] is True
    assert providers["f5tts"]["option_fields"][0]["id"] == "speed"


def test_create_tts_job_rejects_unsupported_style_prompt(monkeypatch):
    monkeypatch.setattr(gateway_app.app.router, "lifespan_context", _test_lifespan)

    with TestClient(gateway_app.app) as client:
        response = client.post(
            "/api/v1/tts/jobs",
            json={
                "provider": "f5tts",
                "text": "hello world",
                "style_prompt": "dramatic and airy",
            },
        )

    assert response.status_code == 400
    assert "style_prompt" in response.text


def test_create_tts_job_routes_f5_request_through_provider(monkeypatch):
    monkeypatch.setattr(gateway_app.app.router, "lifespan_context", _test_lifespan)

    captured: dict = {}

    async def _fake_proxy_to_service(service, path, request, **kwargs):
        captured["service"] = service
        captured["path"] = path
        captured["kwargs"] = kwargs
        return {"job_id": "job-123", "provider": kwargs.get("public_service")}

    monkeypatch.setattr(gateway_app, "proxy_to_service", _fake_proxy_to_service)

    with TestClient(gateway_app.app) as client:
        response = client.post(
            "/api/v1/tts/jobs",
            json={
                "provider": "f5tts",
                "text": "hello world",
                "options": {"speed": 1.15},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"job_id": "job-123", "provider": "f5tts"}
    assert captured["service"] == "f5tts"
    assert captured["path"] == "/synthesize"
    assert captured["kwargs"]["public_service"] == "f5tts"
    assert captured["kwargs"]["history_model_used"] == "F5-TTS"
    assert captured["kwargs"]["json_body"]["text"] == "hello world"
    assert captured["kwargs"]["json_body"]["speed"] == 1.15
