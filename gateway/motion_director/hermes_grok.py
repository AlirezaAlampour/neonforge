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
            "reason": "Existing Hermes video plugin uses the xAI generation API even with subscription OAuth. Automatic Grok generation is disabled under your no-API constraint. Export the Blender MP4, first-frame PNG and prompt, then import a video made in the Grok browser. Existing Hermes profiles are preserved."}
