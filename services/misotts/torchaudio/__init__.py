from __future__ import annotations

from pathlib import Path

import numpy as np
import soxr
import soundfile as sf
import torch
import torch.nn.functional as torch_nn_functional


def load(uri: str | Path, *args, **kwargs):
    data, sample_rate = sf.read(str(uri), dtype="float32", always_2d=True)
    waveform = torch.from_numpy(np.ascontiguousarray(data.T))
    return waveform, int(sample_rate)


def save(uri: str | Path, src: torch.Tensor, sample_rate: int, *args, **kwargs):
    waveform = src.detach().cpu()
    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)
    sf.write(str(uri), np.ascontiguousarray(waveform.T.numpy()), int(sample_rate))


class _FunctionalModule:
    @staticmethod
    def _resample_with_torch(waveform: torch.Tensor, orig_freq: int, new_freq: int) -> torch.Tensor:
        tensor = waveform
        if not tensor.is_floating_point():
            tensor = tensor.float()

        original_dtype = tensor.dtype
        single_channel = tensor.ndim == 1
        if single_channel:
            tensor = tensor.unsqueeze(0)

        batch = tensor.unsqueeze(0)
        target_length = max(1, int(round(batch.shape[-1] * float(new_freq) / float(orig_freq))))
        resampled = torch_nn_functional.interpolate(
            batch,
            size=target_length,
            mode="linear",
            align_corners=False,
        ).squeeze(0)
        resampled = resampled.to(dtype=original_dtype)
        return resampled[0] if single_channel else resampled

    @staticmethod
    def resample(waveform: torch.Tensor, orig_freq: int, new_freq: int, *args, **kwargs) -> torch.Tensor:
        if int(orig_freq) == int(new_freq):
            return waveform

        if waveform.device.type != "cpu":
            return _FunctionalModule._resample_with_torch(waveform, int(orig_freq), int(new_freq))

        tensor = waveform.detach().cpu().float()
        single_channel = tensor.ndim == 1
        if single_channel:
            channels = tensor.unsqueeze(0).numpy()
        else:
            channels = tensor.numpy()

        resampled_channels = [
            soxr.resample(channel, int(orig_freq), int(new_freq), quality="HQ").astype("float32", copy=False)
            for channel in channels
        ]
        resampled = torch.from_numpy(np.ascontiguousarray(np.stack(resampled_channels, axis=0)))
        return resampled[0] if single_channel else resampled


functional = _FunctionalModule()
