import hashlib
import hmac
from pathlib import Path
import secrets
import time


SCOPES = {
    "gateway": {("GET", "/resources"), ("GET", "/claude/status"), ("POST", "/claude/plan")},
    "supervisor": {("GET", "/resources"), ("POST", "/gpu/acquire"), ("POST", "/gpu/release"), ("POST", "/h3/free-idle")},
}


def signature(key: bytes, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = "\n".join([method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest()])
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def signed_headers(key_path: str, role: str, method: str, path: str, body: bytes) -> dict:
    timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
    key = Path(key_path).read_bytes().strip()
    return {"X-NeonForge-Role": role, "X-NeonForge-Time": timestamp, "X-NeonForge-Nonce": nonce,
            "X-NeonForge-Signature": signature(key, method, path, timestamp, nonce, body)}


class Verifier:
    def __init__(self):
        self.seen = {}

    def check(self, headers, method: str, path: str, body: bytes, key_dir: Path) -> bool:
        role = headers.get("X-NeonForge-Role", "")
        if (method, path) not in SCOPES.get(role, set()):
            return False
        timestamp, nonce = headers.get("X-NeonForge-Time", ""), headers.get("X-NeonForge-Nonce", "")
        supplied = headers.get("X-NeonForge-Signature", "")
        now = time.time()
        try:
            if abs(now - int(timestamp)) > 30 or len(nonce) != 32:
                return False
            key = (key_dir / f"{role}.key").read_bytes().strip()
        except (ValueError, OSError):
            return False
        if not hmac.compare_digest(supplied, signature(key, method, path, timestamp, nonce, body)):
            return False
        self.seen = {k: expires for k, expires in self.seen.items() if expires > now}
        if nonce in self.seen or len(self.seen) >= 10000:
            return False
        self.seen[nonce] = now + 60
        return True
