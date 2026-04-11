from __future__ import annotations

import mimetypes
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from .profiles import host_path_to_container

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

    def is_available(self) -> bool:
        return os.getenv("FISH_SPEECH_ENABLED", "false").lower() == "true" and _service_reachable(self.base_url)

    def availability_error(self) -> str:
        return "Fish Speech is not enabled or reachable"

    def synthesize(self, text: str, reference_audio_path: str | None, options: dict[str, Any]) -> bytes:
        if not self.is_available():
            raise ModelUnavailableError(self.availability_error())
        return super().synthesize(text, reference_audio_path, options)


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
        return os.getenv("VOXCPM2_ENABLED", "false").lower() == "true" and _service_reachable(self.base_url)

    def availability_error(self) -> str:
        return "VoxCPM2 is not enabled or reachable"

    def synthesize(self, text: str, reference_audio_path: str | None, options: dict[str, Any]) -> bytes:
        if not self.is_available():
            raise ModelUnavailableError(self.availability_error())
        return super().synthesize(text, reference_audio_path, options)


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
