import asyncio
import sys
from pathlib import Path


GATEWAY_ROOT = Path(__file__).resolve().parents[1] / "gateway"
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

import app as gateway_app


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses: dict[str, _Response]):
        self.responses = responses

    async def get(self, url: str, timeout: float):
        response = self.responses.get(url)
        if response is None:
            raise ConnectionError("service stopped")
        return response


def test_status_blocks_alive_service_with_runtime_error(monkeypatch):
    client = _Client(
        {
            "http://lipsync/healthz": _Response(200, {"status": "alive", "legacy": True}),
            "http://lipsync/readyz": _Response(
                200,
                {
                    "status": "runtime_error",
                    "detail": "inference entry point is missing",
                    "missing": ["/opt/video-retalking/inference.py"],
                },
            ),
        }
    )
    monkeypatch.setattr(gateway_app, "http_client", client)
    monkeypatch.setattr(gateway_app, "rdb", None)

    status = asyncio.run(gateway_app.inspect_service_status("lipsync", "http://lipsync"))

    assert status["alive"] is True
    assert status["ready"] is False
    assert status["state"] == "runtime_error"
    assert status["state_label"] == "Runtime error"
    assert status["legacy"] is True
    assert status["missing"] == ["/opt/video-retalking/inference.py"]


def test_stopped_wan_is_ready_for_supervisor_start(monkeypatch):
    monkeypatch.setattr(gateway_app, "http_client", _Client({}))
    monkeypatch.setattr(gateway_app, "rdb", None)

    status = asyncio.run(gateway_app.inspect_service_status("wan21", "http://wan21"))

    assert status["alive"] is False
    assert status["ready"] is True
    assert status["state"] == "ready"
    assert status["state_label"] == "Ready"
    assert "starts it on demand" in status["detail"]
