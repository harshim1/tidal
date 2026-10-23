"""Signal-processing primitives shared by the respiration and pulse estimators.

Everything here operates on 1-D (or [n_series, n_samples]) float arrays sampled
at a uniform rate ``fs`` (frames per second). The functions are deliberately
small and individually testable; see docs/ARCHITECTURE.md §4 for the maths.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as sps
from scipy import sparse
from scipy.sparse.linalg import spsolve

# --------------------------------------------------------------------------- bands
# Physiological bands in Hz. Respiration: 6–42 breaths/min. Pulse: 42–180 bpm.
RESP_BAND = (0.10, 0.70)
PULSE_BAND = (0.70, 3.00)


def detrend_smoothness_priors(x: np.ndarray, lam: float = 300.0) -> np.ndarray:
    """Tarvainen et al. (2002) smoothness-priors detrending.

    Solves ``z_stat = (I - (I + lam^2 D2^T D2)^-1) z`` where D2 is the second
    difference operator. Removes slow illumination / auto-exposure drift without
    the phase distortion of a high-pass IIR filter. ``lam`` sets the cutoff
    (≈0.04 Hz at fs≈15 for lam=300).
    """
    x = np.asarray(x, dtype=float)
    n = x.shape[-1]
    if n < 4:
        return x - x.mean(axis=-1, keepdims=True)
    ident = sparse.identity(n, format="csc")
    d2 = sparse.diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(n - 2, n), format="csc")
    a = (ident + lam**2 * (d2.T @ d2)).tocsc()
    if x.ndim == 1:
        return x - spsolve(a, x)
    trend = spsolve(a, x.T)
    return x - np.asarray(trend).T


def bandpass(x: np.ndarray, fs: float, band: tuple[float, float], order: int = 3) -> np.ndarray:
    """Zero-phase Butterworth band-pass (second-order sections)."""
    lo, hi = band
    nyq = fs / 2.0
    hi = min(hi, 0.95 * nyq)
    sos = sps.butter(order, [lo / nyq, hi / nyq], btype="bandpass", output="sos")
    padlen = min(x.shape[-1] - 1, 3 * (2 * len(sos) + 1))
    return sps.sosfiltfilt(sos, x, axis=-1, padlen=padlen)


@dataclass(frozen=True)
class SpectralPeak:
    freq_hz: float
    rate_per_min: float
    snr_db: float  # in-band peak power vs. rest of band
    quality: float  # 0..1 signal-quality index derived from snr


def spectral_peak(
    x: np.ndarray, fs: float, band: tuple[float, float], nfft_min: int = 4096
) -> SpectralPeak:
    """Dominant frequency inside ``band`` with sub-bin parabolic interpolation.

    SNR follows de Haan & Jeanne (2013): power within ±0.06 Hz of the peak and
    its first harmonic, divided by the remaining in-band power.
    """
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    n = len(x)
    nfft = max(nfft_min, 1 << int(np.ceil(np.log2(max(n, 2)))))
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(x * win, n=nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, d=1.0 / fs)
    in_band = (freqs >= band[0]) & (freqs <= band[1])
    if not in_band.any() or spec[in_band].sum() <= 0:
        return SpectralPeak(float("nan"), float("nan"), -np.inf, 0.0)
    idx_band = np.flatnonzero(in_band)
    k = idx_band[np.argmax(spec[in_band])]
    # Parabolic interpolation on log power for a sub-bin estimate.
    if 0 < k < len(spec) - 1:
        a, b, c = np.log(spec[k - 1 : k + 2] + 1e-20)
        denom = a - 2 * b + c
        delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
    else:
        delta = 0.0
    f0 = freqs[k] + delta * (freqs[1] - freqs[0])
    tol = 0.06
    sig_mask = in_band & ((np.abs(freqs - f0) <= tol) | (np.abs(freqs - 2 * f0) <= tol))
    noise_mask = in_band & ~sig_mask
    p_sig = spec[sig_mask].sum()
    p_noise = spec[noise_mask].sum() + 1e-20
    snr_db = 10 * np.log10(p_sig / p_noise + 1e-20)
    quality = float(1.0 / (1.0 + np.exp(-(snr_db - 0.0) / 2.0)))  # logistic around 0 dB
    return SpectralPeak(float(f0), float(f0 * 60.0), float(snr_db), quality)


def band_snr(x: np.ndarray, fs: float, band: tuple[float, float]) -> np.ndarray:
    """Vectorised SNR (dB) of the dominant in-band peak for each row of ``x``."""
    x = np.atleast_2d(np.asarray(x, dtype=float))
    x = x - x.mean(axis=1, keepdims=True)
    n = x.shape[1]
    nfft = max(1024, 1 << int(np.ceil(np.log2(n))))
    spec = np.abs(np.fft.rfft(x * np.hanning(n), n=nfft, axis=1)) ** 2
    freqs = np.fft.rfftfreq(nfft, d=1.0 / fs)
    in_band = (freqs >= band[0]) & (freqs <= band[1])
    sb = spec[:, in_band]
    fb = freqs[in_band]
    k = np.argmax(sb, axis=1)
    f0 = fb[k]
    near = np.abs(fb[None, :] - f0[:, None]) <= 0.06
    p_sig = (sb * near).sum(axis=1)
    p_noise = (sb * ~near).sum(axis=1) + 1e-20
    return 10 * np.log10(p_sig / p_noise + 1e-20)


def sliding_rate(
    x: np.ndarray,
    fs: float,
    band: tuple[float, float],
    win_s: float,
    step_s: float,
) -> list[tuple[float, SpectralPeak]]:
    """Rate estimate in overlapping windows → [(window_centre_s, peak), ...]."""
    w = int(round(win_s * fs))
    s = max(1, int(round(step_s * fs)))
    out: list[tuple[float, SpectralPeak]] = []
    if len(x) < w:
        if len(x) >= int(fs * 8):
            out.append((len(x) / fs / 2, spectral_peak(x, fs, band)))
        return out
    for start in range(0, len(x) - w + 1, s):
        seg = x[start : start + w]
        out.append(((start + w / 2) / fs, spectral_peak(seg, fs, band)))
    return out


def amplitude_envelope(x: np.ndarray, fs: float, smooth_s: float = 2.0) -> np.ndarray:
    """Hilbert envelope, smoothed — used to find low-motion (breath-hold) intervals."""
    env = np.abs(sps.hilbert(x))
    k = max(1, int(smooth_s * fs))
    return np.convolve(env, np.ones(k) / k, mode="same")


def low_amplitude_intervals(
    env: np.ndarray, fs: float, rel_thresh: float = 0.3, min_dur_s: float = 8.0
) -> list[tuple[float, float]]:
    """Intervals where the envelope stays below ``rel_thresh`` × its median."""
    ref = np.median(env)
    if ref <= 0:
        return []
    low = env < rel_thresh * ref
    out: list[tuple[float, float]] = []
    i, n = 0, len(low)
    while i < n:
        if low[i]:
            j = i
            while j < n and low[j]:
                j += 1
            if (j - i) / fs >= min_dur_s:
                out.append((i / fs, j / fs))
            i = j
        else:
            i += 1
    return out
