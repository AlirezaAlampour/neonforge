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
    assert "Use Saved Voice Profile" in source
    assert "Record Reference Now" in source
    assert "Style / Control" in source
    assert "Reference Transcript" in source
    assert "payload.vox_mode = voxMode" in source
    assert "payload.prompt_text = trimmedVoxPromptText" in source
    assert "payload.temp_reference_id = voxRecordedReferenceId" in source
    assert "/api/v1/voiceover/temp-reference" in source


def test_voiceover_studio_supports_recorded_voice_profiles_via_the_normal_profile_save_flow():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "type VoiceProfileReferenceSource = 'upload' | 'record'" in source
    assert "Upload Reference Clip" in source
    assert "Record Reference Now" in source
    assert "profileReferenceSource === 'record'" in source
    assert "handleStartProfileRecording" in source
    assert "profileRecorder.audioUrl" in source
    assert "Discard / Re-record" in source
    assert "recording_source', BROWSER_RECORDED_PROFILE_SOURCE" in source
    assert "voice-profile-reference-${Date.now()}.${profileRecorder.fileExtension}" in source
    assert "await refreshProfiles(createdProfile.id)" in source
    assert "Captured as ${profileRecorder.mimeType}" in source
    assert "Use a quiet room and close mic placement" in source
    assert "NeonForge stores a high-quality" in source


def test_voiceover_studio_exposes_profile_recording_device_mode_and_meter_controls():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "const VOICE_PROFILE_INPUT_DEVICE_KEY" in source
    assert "const VOICE_PROFILE_CAPTURE_MODE_KEY" in source
    assert "type VoiceProfileCaptureMode = 'raw' | 'enhanced'" in source
    assert "const enhancedCapture = profileCaptureMode === 'enhanced'" in source
    assert "autoGainControl: enhancedCapture" in source
    assert "echoCancellation: enhancedCapture" in source
    assert "noiseSuppression: enhancedCapture" in source
    assert 'id="voice-profile-input-device"' in source
    assert "Browser default microphone" in source
    assert "Choose the exact mic if the browser default is not the one you want." in source
    assert "Raw Reference Capture" in source
    assert "Enhanced Reference Capture" in source
    assert "Raw = better for accurate voice capture / cloning." in source
    assert "Can sound closer and more polished in untreated rooms." in source
    assert "Live Input Level" in source
    assert "profileRecorder.inputLevel" in source
    assert "profileInputMeterState" in source
