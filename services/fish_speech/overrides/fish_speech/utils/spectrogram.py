import librosa
import numpy as np
import torch
from torch import Tensor, nn


def _linear_resample(x: Tensor, orig_freq: int, new_freq: int) -> Tensor:
    if orig_freq == new_freq:
        return x

    original_dtype = x.dtype
    if x.ndim == 1:
        x = x.unsqueeze(0)

    target_length = max(1, int(round(x.shape[-1] * new_freq / orig_freq)))
    resampled = torch.nn.functional.interpolate(
        x.float().unsqueeze(1),
        size=target_length,
        mode="linear",
        align_corners=False,
    ).squeeze(1)
    return resampled.to(dtype=original_dtype)


class LinearSpectrogram(nn.Module):
    def __init__(
        self,
        n_fft=2048,
        win_length=2048,
        hop_length=512,
        center=False,
        mode="pow2_sqrt",
    ):
        super().__init__()

        self.n_fft = n_fft
        self.win_length = win_length
        self.hop_length = hop_length
        self.center = center
        self.mode = mode
        self.return_complex = True

        self.register_buffer("window", torch.hann_window(win_length), persistent=False)

    def forward(self, y: Tensor) -> Tensor:
        if y.ndim == 3:
            y = y.squeeze(1)

        y = torch.nn.functional.pad(
            y.unsqueeze(1),
            (
                (self.win_length - self.hop_length) // 2,
                (self.win_length - self.hop_length + 1) // 2,
            ),
            mode="reflect",
        ).squeeze(1)

        spec = torch.stft(
            y,
            self.n_fft,
            hop_length=self.hop_length,
            win_length=self.win_length,
            window=self.window,
            center=self.center,
            pad_mode="reflect",
            normalized=False,
            onesided=True,
            return_complex=self.return_complex,
        )

        if self.return_complex:
            spec = torch.view_as_real(spec)

        if self.mode == "pow2_sqrt":
            spec = torch.sqrt(spec.pow(2).sum(-1) + 1e-6)

        return spec


class LogMelSpectrogram(nn.Module):
    def __init__(
        self,
        sample_rate=44100,
        n_fft=2048,
        win_length=2048,
        hop_length=512,
        n_mels=128,
        center=False,
        f_min=0.0,
        f_max=None,
    ):
        super().__init__()

        self.sample_rate = sample_rate
        self.n_fft = n_fft
        self.win_length = win_length
        self.hop_length = hop_length
        self.center = center
        self.n_mels = n_mels
        self.f_min = f_min
        self.f_max = f_max or float(sample_rate // 2)

        self.spectrogram = LinearSpectrogram(n_fft, win_length, hop_length, center)

        mel_filter = librosa.filters.mel(
            sr=self.sample_rate,
            n_fft=self.n_fft,
            n_mels=self.n_mels,
            fmin=self.f_min,
            fmax=self.f_max,
            htk=False,
            norm="slaney",
        ).astype(np.float32)
        fb = torch.from_numpy(mel_filter.T)
        self.register_buffer(
            "fb",
            fb,
            persistent=False,
        )

    def compress(self, x: Tensor) -> Tensor:
        return torch.log(torch.clamp(x, min=1e-5))

    def decompress(self, x: Tensor) -> Tensor:
        return torch.exp(x)

    def apply_mel_scale(self, x: Tensor) -> Tensor:
        return torch.matmul(x.transpose(-1, -2), self.fb).transpose(-1, -2)

    def forward(
        self, x: Tensor, return_linear: bool = False, sample_rate: int = None
    ) -> Tensor:
        if sample_rate is not None and sample_rate != self.sample_rate:
            x = _linear_resample(x, orig_freq=sample_rate, new_freq=self.sample_rate)

        linear = self.spectrogram(x)
        x = self.apply_mel_scale(linear)
        x = self.compress(x)

        if return_linear:
            return x, self.compress(linear)

        return x
