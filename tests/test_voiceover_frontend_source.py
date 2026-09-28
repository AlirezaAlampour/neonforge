from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VOICEOVER_STUDIO_PATH = ROOT / "frontend" / "components" / "VoiceoverStudio.tsx"


def test_voiceover_studio_has_vox_only_mode_controls_and_fields():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "const isVoxModel = selectedModel?.model_id === VOX_MODEL_ID" in source
    assert "hasSelectedModel && isVoxModel && !isVoxContinuationMode && (" in source
    assert "label: 'Design'" in source
    assert "label: 'Clone'" in source
    assert "label: 'Continue'" in source
    assert "hasSelectedModel && isVoxModel && !isVoxContinuationMode && (" in source
    assert "renderContinuationReference()" in source
    assert "handleUseSavedVoiceProfile" in source
    assert "Recorded reference ready." in source
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
    assert "Transcript:" in source
    assert "return profile.reference_transcript?.trim() || ''" in source


def test_voiceover_studio_supports_optional_misotts_prompt_profiles():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "const MISO_MODEL_ID = 'misotts'" in source
    assert "const isMisoModel = selectedModel?.model_id === MISO_MODEL_ID" in source
    assert "!isVoxDesignMode && !voxContinuationUsesRecordedReference && !isMisoModel && !isBreezeDesignMode" in source
    assert "const renderMisoPromptAudioControls = () => (" in source
    assert "!misoUsePromptAudio || !!selectedProfileId" in source
    assert "!misoUsePromptAudio || !!misoPromptText.trim()" in source
    assert "Label htmlFor=\"miso-use-prompt-audio\">Prompt Audio" in source
    assert "Plain text generation can be submitted without a saved voice profile." in source
    assert "Pick a saved voice profile to attach prompt audio." in source
    assert "Required when prompt audio is attached." in source


def test_voiceover_studio_uses_provider_discovery_and_surfaces_misotts_status_and_controls():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "interface VoiceoverProviderSummary" in source
    assert "interface LegacyVoiceoverModelSummary" in source
    assert "/api/v1/voiceover/providers" in source
    assert "/api/v1/voiceover/models" in source
    assert "provider discovery failed, falling back to legacy models" in source
    assert "ensureVisibleFallbackProviders" in source
    assert "MisoTTS 8B" in source
    assert "EXPERIMENTAL" in source
    assert "status_badge" in source
    assert "Service offline" in source
    assert "Loading model" in source
    assert "GPU/VRAM error" in source
    assert "Not installed" in source
    assert "Ready" in source
    assert "gpu_total_vram_gb" in source
    assert "gpu_free_vram_gb" in source
    assert "top_gpu_process" in source
    assert "actionable_hint" in source
    assert "GPU total VRAM" in source
    assert "GPU free VRAM" in source
    assert "Top GPU process" in source
    assert "Hint" in source
    assert "const [misoSpeakerId" in source
    assert "const [misoMaxAudioLengthMs" in source
    assert "const [misoUsePromptAudio" in source
    assert "const [misoPromptText" in source
    assert "Speaker ID" in source
    assert "Max audio length ms" in source
    assert "Prompt Audio" in source
    assert "Prompt Transcript" in source
    assert "payload.speaker_id = misoSpeakerId" in source
    assert "payload.max_audio_length_ms = misoMaxAudioLengthMs" in source
    assert "payload.prompt_text = trimmedMisoPromptText" in source


def test_voiceover_studio_exposes_only_real_breeze_controls_by_mode():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "const BREEZE_MODEL_ID = 'breeze_tts'" in source
    assert "type BreezeMode = 'design' | 'clone' | 'direction'" in source
    assert "const isBreezeDesignMode" in source
    assert "const isBreezeCloneMode" in source
    assert "const isBreezeDirectionMode" in source
    assert "Voice Description" in source
    assert "Exact Reference Transcript" in source
    assert "Transcribe with Whisper" in source
    assert "Save to Profile" in source
    assert "Direction strength" in source
    assert "4 is the recommended starting point" in source
    assert 'id="breeze-seed"' in source
    assert "Randomize" in source
    assert "payload.breeze_mode = breezeMode" in source
    assert "payload.seed = breezeSeed" in source
    assert "payload.cfg_scale = breezeCfgScale" in source
    assert "payload.instruction = trimmedBreezeInstruction" in source
    assert "payload.prompt_text = trimmedBreezeReferenceText" in source
    assert "['(laugh)', '(sigh)', '(cough)', '(clears throat)']" in source
    assert "familiarity" not in source.lower()
    assert "style exaggeration" not in source.lower()


def test_voiceover_studio_uses_creator_focused_progressive_disclosure_for_breeze():
    source = VOICEOVER_STUDIO_PATH.read_text(encoding="utf-8")

    assert "Advanced controls" in source
    assert "BREEZE_DIRECTION_PRESETS" in source
    assert "Natural" in source
    assert "Conversational" in source
    assert "Energetic" in source
    assert "Narrator" in source
    assert "capability_label" in source
    assert "Self-hosted Breeze use is research/non-commercial only." in source
    assert "Keep the seed to compare delivery across script changes." in source


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
    assert "Save as Profile" in source
