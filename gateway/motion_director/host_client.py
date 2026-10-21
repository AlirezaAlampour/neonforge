import httpx
from shared.host_auth import signed_headers


class PlanningRefused(RuntimeError):
    def __init__(self,reason,status_code):
        super().__init__(reason)
        self.status_code=status_code


async def host_request(runtime, path: str, payload=None):
    socket = getattr(runtime, "HOST_CONTROL_SOCKET", "")
    key_path = getattr(runtime, "HOST_CONTROL_KEY", "")
    if not socket or not key_path:
        raise RuntimeError("Authenticated private host adapter is not configured")
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket), timeout=260 if path == "claude/plan" else 100) as client:
        request = client.build_request("POST" if payload is not None else "GET", f"http://localhost/{path}", json=payload)
        request.headers.update(signed_headers(key_path, "gateway", request.method, request.url.path, request.content))
        response = await client.send(request)
    if response.status_code != 200:
        if path=='claude/plan' and response.status_code in {422,503}:
            detail=response.json().get('detail')
            if isinstance(detail,str):raise PlanningRefused(detail[:600],response.status_code)
        raise RuntimeError("Authenticated host adapter refused the request")
    return response.json()
