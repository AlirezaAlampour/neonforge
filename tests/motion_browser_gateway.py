"""Loopback-only gateway fixture for the real frontend browser tests.

Run with the repository's verified uv environment and temporary storage paths.
No Redis, supervisor, H3, Hermes, cloud service, or GPU is contacted.
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
from test_motion_director import MockH3, gateway
from motion_director.routes import shutdown_motion_jobs


class BrowserH3(MockH3):
    def handle(self, request):
        if request.url.path == "/view":
            return httpx.Response(200, content=Path("/tmp/neonforge-motion-ui-20261012.mp4").read_bytes(), headers={"Content-Type": "video/mp4"})
        return super().handle(request)


@asynccontextmanager
async def lifespan(app):
    gateway.Base.metadata.create_all(gateway.db_engine)
    gateway.http_client = httpx.AsyncClient(transport=httpx.MockTransport(BrowserH3().handle))
    gateway.H3_COMFYUI_URL = "http://mock-h3"
    gateway.SUPERVISOR_URL = "http://mock-supervisor"
    gateway.get_memory_status = lambda: {"available_gb": 95}
    gateway.gpu_heavy_sem = asyncio.Semaphore(1)
    gateway.COMFYUI_POLL_INTERVAL_SEC = .01
    yield
    await shutdown_motion_jobs()
    await gateway.http_client.aclose()


if __name__ == "__main__":
    gateway.app.router.lifespan_context = lifespan
    uvicorn.run(gateway.app, host="127.0.0.1", port=18081, log_level="warning")
