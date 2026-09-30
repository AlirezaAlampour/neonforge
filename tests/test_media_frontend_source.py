from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_primary_navigation_is_workflow_first():
    source = (ROOT / "frontend" / "components" / "sidebar.tsx").read_text(encoding="utf-8")

    for label in ("Voiceover", "Video", "Character", "Avatar", "Lip Sync", "System Info"):
        assert f"label: '{label}'" in source

    assert "label: 'Creative Studio'" not in source
    assert "label: 'Voice Studio'" not in source


def test_unavailable_media_workflows_surface_readiness():
    lip_sync = (ROOT / "frontend" / "app" / "lipsync" / "page.tsx").read_text(encoding="utf-8")
    video = (ROOT / "frontend" / "app" / "video" / "page.tsx").read_text(encoding="utf-8")
    avatar = (ROOT / "frontend" / "app" / "avatar" / "page.tsx").read_text(encoding="utf-8")

    assert "services?.lipsync?.ready" in lip_sync
    assert "!ready || busy" in lip_sync
    assert "memory.available_gb >= memory.thresholds.reserve_heavy_gb" not in video
    assert "hunyuan-video-15-t2v" in video
    assert "currently unavailable" in avatar
    assert "submit" not in avatar
    for old_route in ("studio", "voice", "broll"):
        assert not (ROOT / "frontend" / "app" / old_route / "page.tsx").exists()


def test_system_status_proxies_workload_lifecycle_endpoint():
    next_config = (ROOT / "frontend" / "next.config.mjs").read_text(encoding="utf-8")

    assert "source: '/workloads/status'" in next_config
    assert "destination: `${gateway}/workloads/status`" in next_config
