from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VOICEOVER_STUDIO_PATH = ROOT / "frontend" / "components" / "VoiceoverStudio.tsx"


def test_voiceover_studio_has_vox_only_mode_controls_and_fields():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "const isVoxModel = selectedModel?.model_id === VOX_MODEL_ID" in source
    assert "{isVoxModel && (" in source
    assert "Voice Design" in source
    assert "Clone My Voice" in source
    assert "Continue From Reference" in source
    assert "{isVoxModel && !isVoxContinuationMode && (" in source
    assert "{isVoxContinuationMode && (" in source
    assert "Style / Control" in source
    assert "Reference Transcript" in source
    assert "payload.vox_mode = voxMode" in source
    assert "payload.prompt_text = trimmedVoxPromptText" in source
