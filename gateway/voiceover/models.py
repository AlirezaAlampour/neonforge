from __future__ import annotations

import base64
import mimetypes
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from .profiles import get_profile, host_path_to_container, update_profile_reference_transcript

HOST_OUTPUTS_ROOT = Path("/srv/ai/outputs")
CONTAINER_OUTPUTS_ROOT = Path(os.getenv("OUTPUTS_ROOT", "/outputs"))


class ModelUnavailableError(RuntimeError):
    pass


def _service_reachable(base_url: str) -> bool:
    url = base_url.rstrip("/")
    health_url = f"{url}/healthz"
    try:
        response = httpx.get(health_url, timeout=httpx.Timeout(3.0, connect=2.0))
        if response.status_code == 200:
            return True
    except Exception:
        pass

    try:
        response = httpx.get(url, timeout=httpx.Timeout(3.0, connect=2.0))
        return response.status_code < 500
    except Exception:
        return False


def _resolve_output_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.exists():
        return path

    try:
        relative = path.relative_to(HOST_OUTPUTS_ROOT)
        return CONTAINER_OUTPUTS_ROOT / relative
    except ValueError:
        pass

    if path.is_absolute():
        return path
    return CONTAINER_OUTPUTS_ROOT / path


def _build_vox_text(text: str, style_text: str | None) -> str:
    cleaned_text = text.strip()
    cleaned_style = (style_text or "").strip()
    if not cleaned_style:
        return cleaned_text
    return f"({cleaned_style}){cleaned_text}"


class VoiceoverModel(ABC):
    @property
    @abstractmethod
    def model_id(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def display_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def supports_reference_audio(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    def availability_error(self) -> str:
        return f"{self.display_name} is unavailable"

    @abstractmethod
    def synthesize(self, text: str, reference_audio_path: str | None, options: dict[str, Any]) -> bytes:
        raise NotImplementedError


class _HTTPVoiceoverModel(VoiceoverModel):
    @property
    @abstractmethod
    def base_url(self) -> str:
        raise NotImplementedError

    def _request_data(self, text: str, options: dict[str, Any]) -> dict[str, str]:
        data = {"text": text}

        speed = options.get("speed")
        if speed is not None:
            data["speed"] = str(speed)

        for key, value in options.items():
            if key == "speed" or value is None:
                continue
            data[key] = str(value)

        return data

    def synthesize(self, text: str, reference_audio_path: str | None, options: dict[str, Any]) -> bytes:
        if not self.is_available():
            raise ModelUnavailableError(self.availability_error())

        data = self._request_data(text, options)

        files: dict[str, tuple[str, bytes, str]] = {}
        if reference_audio_path:
            container_audio_path = host_path_to_container(reference_audio_path)
            audio_bytes = container_audio_path.read_bytes()
            audio_name = container_audio_path.name
            media_type = mimetypes.guess_type(audio_name)[0] or "application/octet-stream"
            files["reference_audio"] = (audio_name, audio_bytes, media_type)
            files["ref_audio"] = (audio_name, audio_bytes, media_type)

        response = httpx.post(
            f"{self.base_url.rstrip('/')}/synthesize",
            data=data,
            files=files or None,
            timeout=httpx.Timeout(300.0, connect=10.0),
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "audio/" in content_type or response.content[:4] == b"RIFF":
            return response.content

        payload = response.json()
        output_path = payload.get("output_path")
        if not output_path:
            raise ModelUnavailableError(f"{self.display_name} did not return audio bytes or an output path")

        return _resolve_output_path(str(output_path)).read_bytes()


class F5TTSModel(_HTTPVoiceoverModel):
    @property
    def model_id(self) -> str:
        return "f5tts"

    @property
    def display_name(self) -> str:
        return "F5-TTS (Local)"

    @property
    def supports_reference_audio(self) -> bool:
        return True

    @property
    def base_url(self) -> str:
        return os.getenv("F5TTS_INTERNAL_URL", "http://f5tts:8000")

    def is_available(self) -> bool:
        return _service_reachable(self.base_url)

    def availability_error(self) -> str:
        return "F5-TTS is unreachable right now"


class FishSpeechModel(_HTTPVoiceoverModel):
    def __init__(self) -> None:
        self._reference_text_cache: dict[str, str] = {}

    @property
    def model_id(self) -> str:
        return "fish_speech"

    @property
    def display_name(self) -> str:
        return "Fish Speech 1.5 (Local)"

    @property
    def supports_reference_audio(self) -> bool:
        return True

    @property
    def base_url(self) -> str:
        return os.getenv("FISH_SPEECH_INTERNAL_URL", "http://fish_speech:8000")

    @property
    def whisper_url(self) -> str:
        return os.getenv("WHISPER_URL", "http://whisper:8000")

    def is_available(self) -> bool:
        if os.getenv("FISH_SPEECH_ENABLED", "false").lower() != "true":
            return False

        try:
            response = httpx.get(
                f"{self.base_url.rstrip('/')}/v1/health",
                timeout=httpx.Timeout(5.0, connect=3.0),
            )
            payload = response.json()
            return response.status_code == 200 and payload.get("status") == "ok"
        except Exception:
            return False

    def availability_error(self) -> str:
        return "Fish Speech is not enabled or reachable"

    def _cache_reference_text(self, cache_key: str, text: str, profile_id: str | None) -> str:
        cleaned_text = text.strip()
        if not cleaned_text:
            raise ModelUnavailableError("Fish Speech could not transcribe the reference audio")

        self._reference_text_cache[cache_key] = cleaned_text
        if profile_id:
            self._reference_text_cache[f"profile:{profile_id}"] = cleaned_text
            try:
                update_profile_reference_transcript(profile_id, cleaned_text)
            except Exception:
                pass

        return cleaned_text

    def _transcribe_reference_audio(
        self,
        audio_name: str,
        audio_bytes: bytes,
        media_type: str,
        profile_id: str | None = None,
    ) -> str:
        profile_cache_key = f"profile:{profile_id}" if profile_id else None
        if profile_cache_key:
            cached = self._reference_text_cache.get(profile_cache_key)
            if cached:
                return cached

            profile = get_profile(profile_id)
            if profile and profile.reference_transcript:
                cleaned_text = profile.reference_transcript.strip()
                if cleaned_text:
                    self._reference_text_cache[profile_cache_key] = cleaned_text
                    return cleaned_text

        cache_key = f"{audio_name}:{len(audio_bytes)}"
        cached = self._reference_text_cache.get(cache_key)
        if cached:
            return cached

        try:
            response = httpx.post(
                f"{self.whisper_url.rstrip('/')}/transcribe",
                files={"audio": (audio_name, audio_bytes, media_type)},
                timeout=httpx.Timeout(180.0, connect=10.0),
            )
            response.raise_for_status()
            payload = response.json()
            text = str(payload.get("text") or "").strip()
        except Exception as exc:
            raise ModelUnavailableError("Fish Speech could not transcribe the reference audio") from exc

        return self._cache_reference_text(cache_key, text, profile_id)

    def synthesize(self, text: str, reference_audio_path: str | None, options: dict[str, Any]) -> bytes:
        if not self.is_available():
            raise ModelUnavailableError(self.availability_error())

        references: list[dict[str, str]] = []
        if reference_audio_path:
            container_audio_path = host_path_to_container(reference_audio_path)
            audio_bytes = container_audio_path.read_bytes()
            audio_name = container_audio_path.name
            media_type = mimetypes.guess_type(audio_name)[0] or "application/octet-stream"
            profile_id = str(options.get("voice_profile_id") or "").strip() or None
            reference_text = self._transcribe_reference_audio(audio_name, audio_bytes, media_type, profile_id)
            references.append(
                {
                    "audio": base64.b64encode(audio_bytes).decode("utf-8"),
                    "text": reference_text,
                }
            )

        payload = {
            "text": text,
            "chunk_length": 200,
            "format": "wav",
            "references": references,
            "reference_id": None,
            "normalize": True,
            "streaming": False,
            "use_memory_cache": "on" if references else "off",
        }

        try:
            response = httpx.post(
                f"{self.base_url.rstrip('/')}/v1/tts",
                json=payload,
                timeout=httpx.Timeout(600.0, connect=10.0),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelUnavailableError("Fish Speech synthesis failed") from exc

        return response.content


class VoxCPM2Model(_HTTPVoiceoverModel):
    @property
    def model_id(self) -> str:
        return "voxcpm2"

    @property
    def display_name(self) -> str:
        return "VoxCPM2 (Local)"

    @property
    def supports_reference_audio(self) -> bool:
        return True

    @property
    def base_url(self) -> str:
        return os.getenv("VOXCPM2_INTERNAL_URL", "http://voxcpm2:8000")

    def is_available(self) -> bool:
        if os.getenv("VOXCPM2_ENABLED", "false").lower() != "true":
            return False

        if not _service_reachable(self.base_url):
            return False

        try:
            response = httpx.get(
                f"{self.base_url.rstrip('/')}/v1/health",
                timeout=httpx.Timeout(5.0, connect=3.0),
            )
            payload = response.json()
            return response.status_code == 200 and payload.get("status") == "ok"
        except Exception:
            return False

    def availability_error(self) -> str:
        return "VoxCPM2 is not enabled or the runtime is unreachable/unhealthy"

    def synthesize(self, text: str, reference_audio_path: str | None, options: dict[str, Any]) -> bytes:
        if not self.is_available():
            raise ModelUnavailableError(self.availability_error())

        vox_mode = str(options.get("vox_mode") or "clone").strip().lower() or "clone"
        prompt_text = str(options.get("prompt_text") or "").strip()
        style_text = str(options.get("style_text") or "").strip()

        data = {
            "text": _build_vox_text(text, style_text if vox_mode != "continuation" else None),
            "vox_mode": vox_mode,
        }

        files: dict[str, tuple[str, bytes, str]] = {}
        if vox_mode == "design":
            reference_audio_path = None
        elif vox_mode in {"clone", "continuation"}:
            if not reference_audio_path:
                raise ModelUnavailableError(f"VoxCPM2 {vox_mode} mode requires a reference audio clip")

            container_audio_path = host_path_to_container(reference_audio_path)
            reference_bytes = container_audio_path.read_bytes()
            audio_name = container_audio_path.name
            media_type = mimetypes.guess_type(audio_name)[0] or "application/octet-stream"
            files["reference_audio"] = (audio_name, reference_bytes, media_type)

            if vox_mode == "continuation":
                if not prompt_text:
                    raise ModelUnavailableError("VoxCPM2 continuation mode requires the reference transcript")
                data["prompt_text"] = prompt_text
        else:
            raise ModelUnavailableError(f"Unsupported VoxCPM2 mode: {vox_mode}")

        response = httpx.post(
            f"{self.base_url.rstrip('/')}/synthesize",
            data=data,
            files=files or None,
            timeout=httpx.Timeout(600.0, connect=10.0),
        )
        response.raise_for_status()
        return response.content


class PremiumCloneModel(VoiceoverModel):
    @property
    def model_id(self) -> str:
        return "premium_clone"

    @property
    def display_name(self) -> str:
        return "Premium Clone (Scaffold)"

    @property
    def supports_reference_audio(self) -> bool:
        return True

    def is_available(self) -> bool:
        return False

    def availability_error(self) -> str:
        return "Premium Clone is not yet implemented"

    def synthesize(self, text: str, reference_audio_path: str | None, options: dict[str, Any]) -> bytes:
        # TODO: wire to actual model when runtime is confirmed
        raise ModelUnavailableError("Not yet implemented")


class ModelRegistry:
    @staticmethod
    def all_models() -> list[VoiceoverModel]:
        return [
            F5TTSModel(),
            FishSpeechModel(),
            VoxCPM2Model(),
            PremiumCloneModel(),
        ]

    @classmethod
    def available_models(cls) -> list[VoiceoverModel]:
        return [model for model in cls.all_models() if model.is_available()]

    @classmethod
    def get_model(cls, model_id: str) -> VoiceoverModel | None:
        for model in cls.all_models():
            if model.model_id == model_id:
                return model
        return None
