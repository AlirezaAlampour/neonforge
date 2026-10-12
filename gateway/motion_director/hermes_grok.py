"""Read-only bridge to the existing Hermes video plugin. No agent or auth clone."""
import json
import os
from pathlib import Path

ROOT = Path(os.getenv("NEONFORGE_HERMES_ROOT", str(Path.home() / "pinokio/api/hermes-agent.pinokio.git/app")))


def status():
    plugin = ROOT / "plugins/video_gen/xai/__init__.py"
    launcher = ROOT / "env/bin/hermes"
    auth = Path.home() / ".hermes/auth.json"
    installed = plugin.is_file() and launcher.is_file()
    # Never resolve the plugin's API-key fallback, refresh credentials, invoke a
    # chat agent, or make generation calls merely to determine availability.
    oauth_present = False
    if auth.is_file():
        try:
            data = json.loads(auth.read_text())
            providers = data.get("providers", {})
            entry = providers.get("xai-oauth", {})
            oauth_present = bool(entry.get("access_token") or entry.get("refresh_token"))
        except (ValueError, OSError):
            pass
    return {"available": False, "connector": "existing-hermes-video-plugin", "installed": installed,
            "oauth_present": oauth_present, "authorization_required": not oauth_present, "manual_import": True,
            "authorization_url": None,
            "reason": ("Existing Hermes xAI video plugin found; Grok OAuth is not saved. Launch the existing Pinokio Hermes app and authorize xai-oauth using existing SuperGrok access. No API-key fallback or new subscription is allowed. Manual import/compare is available."
                       if installed and not oauth_present else "Hermes Grok video entitlement/no-cost access is unverified. Generation remains disabled; manual import/compare is available.")}
