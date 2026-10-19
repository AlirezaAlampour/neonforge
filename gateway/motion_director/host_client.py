import httpx
from shared.host_auth import signed_headers


async def host_request(runtime, path: str, payload=None):
    socket = getattr(runtime, "HOST_CONTROL_SOCKET", "")
    key_path = getattr(runtime, "HOST_CONTROL_KEY", "")
    if not socket or not key_path:
        raise RuntimeError("Authenticated private host adapter is not configured")
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket), timeout=200 if path == "claude/plan" else 100) as client:
        request = client.build_request("POST" if payload is not None else "GET", f"http://localhost/{path}", json=payload)
        request.headers.update(signed_headers(key_path, "gateway", request.method, request.url.path, request.content))
        response = await client.send(request)
    if response.status_code != 200:
        raise RuntimeError("Authenticated host adapter refused the request")
    return response.json()
