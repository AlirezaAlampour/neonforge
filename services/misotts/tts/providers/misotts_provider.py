from __future__ import annotations

import importlib
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Match upstream run_misotts.py: avoid Triton-backed torch compile paths.
os.environ.setdefault("NO_TORCH_COMPILE", "1")

log = logging.getLogger("misotts.provider")

DEFAULT_SOURCE_DIR = Path(os.getenv("MISOTTS_CHECKOUT_DIR", "/opt/misotts-src"))
DEFAULT_MODEL_ID = os.getenv("MISOTTS_MODEL_ID", "MisoLabs/MisoTTS")
DEFAULT_DEVICE = os.getenv("MISOTTS_DEVICE", "auto").strip().lower() or "auto"
DEFAULT_MAX_AUDIO_MS = int(os.getenv("MISOTTS_MAX_AUDIO_MS", "10000"))
DEFAULT_ENABLE_VOICE_PROMPT = os.getenv("MISOTTS_ENABLE_VOICE_PROMPT", "true").strip().lower() == "true"
DEFAULT_ENABLE_WATERMARK = os.getenv("MISOTTS_ENABLE_WATERMARK", "true").strip().lower() == "true"
DEFAULT_MIN_CUDA_FREE_GB = float(os.getenv("MISOTTS_MIN_CUDA_FREE_GB", "12"))


@dataclass
class MisoSynthesisResult:
    audio_bytes: bytes | None
    output_path: str | None
    sample_rate: int


class MisoTTSProvider:
    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        device: str = DEFAULT_DEVICE,
        default_max_audio_length_ms: int = DEFAULT_MAX_AUDIO_MS,
        enable_voice_prompt: bool = DEFAULT_ENABLE_VOICE_PROMPT,
        enable_watermark: bool = DEFAULT_ENABLE_WATERMARK,
        source_dir: str | Path = DEFAULT_SOURCE_DIR,
        min_cuda_free_gb: float = DEFAULT_MIN_CUDA_FREE_GB,
    ) -> None:
        self.model_id = model_id
        self.device_preference = device
        self.default_max_audio_length_ms = max(1, int(default_max_audio_length_ms))
        self.enable_voice_prompt = enable_voice_prompt
        self.enable_watermark = enable_watermark
        self.source_dir = Path(source_dir)
        self.min_cuda_free_gb = float(min_cuda_free_gb)
        self._generator: Any | None = None
        self._resolved_device: str | None = None

    def is_loaded(self) -> bool:
        return self._generator is not None

    def resolved_device(self) -> str | None:
        return self._resolved_device

    def _import_module(self, module_name: str) -> Any:
        return importlib.import_module(module_name)

    def _generator_model(self, generator: Any) -> Any | None:
        return getattr(generator, "_model", None) or getattr(generator, "model", None)

    def _generator_device(self, generator: Any, fallback: str | None = None) -> str | Any | None:
        device = getattr(generator, "device", None)
        if device is not None:
            return device

        model = self._generator_model(generator)
        if model is None:
            return fallback

        try:
            return next(model.parameters()).device
        except Exception:
            return fallback

    def _to_device_audio(self, audio: Any, device: str | Any | None) -> Any:
        if audio is None or device is None or not hasattr(audio, "to"):
            return audio
        return audio.to(device=device)

    def _configure_generator_module(self, generator_module: Any) -> None:
        if self.enable_watermark:
            return

        def _passthrough_load_watermarker(*args, **kwargs):
            return None

        def _passthrough_watermark(watermarker, audio, sample_rate, watermark_key):
            return audio, sample_rate

        generator_module.load_watermarker = _passthrough_load_watermarker
        generator_module.watermark = _passthrough_watermark

    def _move_generator_runtime_to_device(self, generator: Any, resolved_device: str) -> None:
        model = self._generator_model(generator)
        if model is not None:
            model.to(device=resolved_device)
        if hasattr(generator, "device"):
            generator.device = next(model.parameters()).device if model is not None else resolved_device

    def _log_generator_layout(self, generator: Any) -> None:
        model = self._generator_model(generator)
        if model is None:
            log.info("Loaded MisoTTS generator on device=%s", self._generator_device(generator, self._resolved_device))
            return

        parameter_devices: list[str] = []
        for name, param in list(model.named_parameters())[:5]:
            descriptor = f"{name}={param.device}:{param.dtype}"
            parameter_devices.append(descriptor)
        log.info(
            "Loaded MisoTTS generator on device=%s watermark=%s params=%s",
            self._generator_device(generator, self._resolved_device),
            "enabled" if self.enable_watermark else "disabled",
            ", ".join(parameter_devices) or "<none>",
        )

    def _install_source_path(self) -> None:
        if self.source_dir.exists() and str(self.source_dir) not in sys.path:
            sys.path.insert(0, str(self.source_dir))

    def _resolve_device(self) -> str:
        if self.device_preference not in {"auto", "cuda", "cpu"}:
            raise RuntimeError("MISOTTS_DEVICE must be auto, cuda, or cpu")

        torch = self._import_module("torch")
        cuda_available = bool(getattr(torch.cuda, "is_available", lambda: False)())

        if self.device_preference == "cuda":
            if not cuda_available:
                raise RuntimeError(
                    "CUDA is unavailable for MisoTTS. Set MISOTTS_DEVICE=cpu for an experimental CPU run "
                    "or provision a CUDA-capable host."
                )
            return "cuda"

        if self.device_preference == "cpu":
            return "cpu"

        return "cuda" if cuda_available else "cpu"

    def _verify_cuda_memory(self, torch: Any) -> None:
        if self.min_cuda_free_gb <= 0:
            return

        try:
            free_bytes, _total_bytes = torch.cuda.mem_get_info()
        except Exception:
            return

        free_gb = free_bytes / float(1024**3)
        if free_gb < self.min_cuda_free_gb:
            raise RuntimeError(
                f"Insufficient free CUDA memory for MisoTTS: {free_gb:.1f} GB available; "
                f"need roughly {self.min_cuda_free_gb:.1f}+ GB free."
            )

    def _load_generator(self) -> Any:
        self._install_source_path()

        if not self.source_dir.exists():
            raise FileNotFoundError(
                f"MisoTTS source checkout was not found at {self.source_dir}. "
                "Clone https://github.com/MisoLabsAI/MisoTTS there or mount an external checkout."
            )

        torch = self._import_module("torch")
        resolved_device = self._resolve_device()
        if resolved_device == "cuda":
            self._verify_cuda_memory(torch)

        generator_module = self._import_module("generator")
        self._configure_generator_module(generator_module)
        load_miso_8b = getattr(generator_module, "load_miso_8b")

        generator = load_miso_8b(
            device=resolved_device,
            model_path_or_repo_id=self.model_id,
        )
        self._move_generator_runtime_to_device(generator, resolved_device)

        self._resolved_device = str(self._generator_device(generator, resolved_device))
        self._log_generator_layout(generator)
        return generator

    def _ensure_generator(self) -> Any:
        if self._generator is None:
            self._generator = self._load_generator()
        return self._generator

    def _context_from_reference(
        self,
        *,
        generator: Any,
        reference_audio: tuple[str, bytes] | None,
        prompt_text: str | None,
        speaker: int,
    ) -> list[Any]:
        if reference_audio is None:
            return []

        if not self.enable_voice_prompt:
            raise RuntimeError("Voice prompt conditioning is disabled. Set MISOTTS_ENABLE_VOICE_PROMPT=true to enable it.")

        cleaned_prompt_text = (prompt_text or "").strip()
        if not cleaned_prompt_text:
            raise RuntimeError("Prompt audio requires the exact transcript of the reference clip.")

        self._install_source_path()
        generator_module = self._import_module("generator")
        Segment = getattr(generator_module, "Segment")
        torchaudio = self._import_module("torchaudio")

        filename, audio_bytes = reference_audio
        suffix = Path(filename).suffix or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_audio:
            temp_audio.write(audio_bytes)
            temp_path = Path(temp_audio.name)

        try:
            waveform, sample_rate = torchaudio.load(str(temp_path))
        finally:
            temp_path.unlink(missing_ok=True)

        if waveform.ndim != 2:
            raise RuntimeError("Prompt audio could not be decoded into a waveform")

        if waveform.size(0) > 1:
            waveform = waveform.mean(dim=0, keepdim=True)

        prompt_waveform = waveform[0]
        if sample_rate != generator.sample_rate:
            prompt_waveform = torchaudio.functional.resample(
                prompt_waveform,
                orig_freq=sample_rate,
                new_freq=generator.sample_rate,
            )

        prompt_waveform = self._to_device_audio(
            prompt_waveform,
            self._generator_device(generator, self._resolved_device),
        )
        return [Segment(speaker=speaker, text=cleaned_prompt_text, audio=prompt_waveform)]

    def _save_waveform(self, waveform: Any, *, sample_rate: int, destination: Path) -> None:
        torchaudio = self._import_module("torchaudio")

        destination.parent.mkdir(parents=True, exist_ok=True)
        save_waveform = waveform.detach().cpu()
        if getattr(save_waveform, "ndim", 1) == 1:
            save_waveform = save_waveform.unsqueeze(0)
        torchaudio.save(str(destination), save_waveform, sample_rate)

    def synthesize(
        self,
        *,
        text: str,
        speaker: int = 0,
        max_audio_length_ms: int | None = None,
        prompt_text: str | None = None,
        reference_audio: tuple[str, bytes] | None = None,
        output_wav_path: str | None = None,
    ) -> MisoSynthesisResult:
        cleaned_text = text.strip()
        if not cleaned_text:
            raise RuntimeError("Text must not be empty")

        generator = self._ensure_generator()
        effective_speaker = int(speaker)
        if effective_speaker < 0:
            raise RuntimeError("speaker must be 0 or greater")

        effective_max_audio_length_ms = int(max_audio_length_ms or self.default_max_audio_length_ms)
        if effective_max_audio_length_ms <= 0:
            raise RuntimeError("max_audio_length_ms must be greater than zero")

        context = self._context_from_reference(
            generator=generator,
            reference_audio=reference_audio,
            prompt_text=prompt_text,
            speaker=effective_speaker,
        )

        try:
            waveform = generator.generate(
                text=cleaned_text,
                speaker=effective_speaker,
                context=context,
                max_audio_length_ms=effective_max_audio_length_ms,
            )
        except Exception as exc:
            message = str(exc).strip().lower()
            if "same device" in message or "at least two devices" in message or "cuda:0 and cpu" in message:
                raise RuntimeError(
                    "MisoTTS hit a model runtime device mismatch between CUDA and CPU tensors. "
                    f"Original error: {exc}"
                ) from exc
            if "out of memory" in message:
                raise RuntimeError(
                    "MisoTTS ran out of CUDA memory while generating audio. Close other GPU workloads "
                    "or increase available VRAM/UMA before retrying."
                ) from exc
            raise

        sample_rate = int(generator.sample_rate)

        if output_wav_path:
            destination = Path(output_wav_path)
            if not destination.is_absolute():
                destination = Path(os.getenv("OUTPUT_DIR", "/outputs/tts")) / destination
            self._save_waveform(waveform, sample_rate=sample_rate, destination=destination)
            return MisoSynthesisResult(
                audio_bytes=None,
                output_path=str(destination),
                sample_rate=sample_rate,
            )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_output:
            output_path = Path(temp_output.name)

        try:
            self._save_waveform(waveform, sample_rate=sample_rate, destination=output_path)
            return MisoSynthesisResult(
                audio_bytes=output_path.read_bytes(),
                output_path=None,
                sample_rate=sample_rate,
            )
        finally:
            output_path.unlink(missing_ok=True)
