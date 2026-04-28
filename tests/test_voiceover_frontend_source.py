from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VOICEOVER_STUDIO_PATH = ROOT / "frontend" / "components" / "VoiceoverStudio.tsx"


def test_voiceover_studio_has_vox_only_mode_controls_and_fields():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "const isVoxModel = selectedModel?.model_id === VOX_MODEL_ID" in source
    assert "hasSelectedModel && isVoxModel && (" in source
    assert "label: 'Design'" in source
    assert "label: 'Clone'" in source
    assert "label: 'Continue'" in source
    assert "hasSelectedModel && isVoxModel && !isVoxContinuationMode && (" in source
    assert "hasSelectedModel && isVoxContinuationMode && (" in source
    assert "handleUseSavedVoiceProfile" in source
    assert "Record Reference" in source
    assert "Style / Control" in source
    assert "Reference Transcript" in source
    assert "payload.vox_mode = voxMode" in source
    assert "payload.prompt_text = trimmedVoxPromptText" in source
    assert "payload.temp_reference_id = voxRecordedReferenceId" in source
    assert "/api/v1/voiceover/temp-reference" in source


def test_voiceover_studio_supports_recorded_voice_profiles_via_the_normal_profile_save_flow():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "type VoiceProfileReferenceSource = 'upload' | 'record'" in source
    assert "Upload" in source
    assert "Record" in source
    assert "profileReferenceSource === 'record'" in source
    assert "handleStartProfileRecording" in source
    assert "profileRecorder.audioUrl" in source
    assert "Re-record" in source
    assert "recording_source', BROWSER_RECORDED_PROFILE_SOURCE" in source
    assert "voice-profile-reference-${Date.now()}.${profileRecorder.fileExtension}" in source
    assert "await refreshProfiles(createdProfile.id)" in source
    assert "Captured as ${profileRecorder.mimeType}" in source
    assert "Short, clean reference clips work best." in source


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
    assert "Pick a mic when the default is wrong." in source
    assert "Best clone source." in source
    assert "Cleaner room sound." in source
    assert "Raw is most faithful; enhanced is cleaner." in source
    assert "Live Input Level" in source
    assert "profileRecorder.inputLevel" in source
    assert "profileInputMeterState" in source


def test_voiceover_studio_uses_profile_reference_transcripts_for_profiles_and_continuation_defaults():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "reference_transcript?: string | null" in source
    assert "function getProfileTranscriptSeed" in source
    assert "lastAutoSeededVoxPromptRef" in source
    assert "Profile filled" in source
    assert "Reference Transcript" in source
    assert "Stored transcript:" in source
    assert "return profile.reference_transcript?.trim() || ''" in source


def test_voiceover_studio_outputs_tab_supports_bulk_selection_delete_and_download():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "selectedOutputIds" in source
    assert "toggleSelectAllRecentVoiceovers" in source
    assert "handleBulkDownloadRecentVoiceovers" in source
    assert "handleBulkDeleteRecentVoiceovers" in source
    assert "Download Selected" in source
    assert "Delete Selected" in source
    assert "Select all" in source
    assert "Download Text" in source
    assert "Download Metadata" in source
