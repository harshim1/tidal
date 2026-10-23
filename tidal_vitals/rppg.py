"""Remote photoplethysmography (rPPG): pulse rate from skin colour.

Each heartbeat pushes blood into the facial micro-vasculature; haemoglobin
absorbs green light most strongly, so skin reflectance oscillates by ~0.1–1%
at the heart rate. We implement the Plane-Orthogonal-to-Skin (POS) algorithm
of Wang, den Brinker, Stuijk & de Haan (IEEE TBME 2017):

    Cn(t)  = C(t) / mean_window(C)                 temporal normalisation
    S      = [[0, 1, -1], [-2, 1, 1]] · Cn         project onto plane ⟂ skin tone
    h      = S1 + (σ(S1)/σ(S2)) · S2               alpha-tuning cancels specular/motion
    pulse  = overlap-add of h over 1.6 s windows

POS is training-free and among the most robust classical methods on public
benchmarks (UBFC-rPPG, PURE). Treat it as *experimental* on compressed,
distant, or IR-night footage — the colour signal there is often below the
codec's quantisation step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from . import signal as sig
from .blocks import block_means, gross_motion_mask, to_gray

_P = np.array([[0.0, 1.0, -1.0], [-2.0, 1.0, 1.0]])


@dataclass
class PulseResult:
    rate_bpm: float | None
    quality: float
    snr_db: float
    skin_fraction: float
    windows: list[dict] = field(default_factory=list)
    roi_blocks: list[int] = field(default_factory=list)
    grid: tuple[int, int] = (0, 0)
    block_px: int = 16
    experimental: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def pos(rgb: np.ndarray, fs: float, win_s: float = 1.6) -> np.ndarray:
    """POS for many series at once. rgb[n, T, 3] (linear-ish RGB means) → pulse[n, T]."""
    n, t, _ = rgb.shape
    l = max(2, int(round(win_s * fs)))
    h = np.zeros((n, t))
    for start in range(0, t - l + 1):
        c = rgb[:, start : start + l, :]  # [n, l, 3]
        cn = c / (c.mean(axis=1, keepdims=True) + 1e-9)
        s = np.einsum("ij,nlj->nil", _P, cn)  # [n, 2, l]
        s1, s2 = s[:, 0, :], s[:, 1, :]
        alpha = s1.std(axis=1, keepdims=True) / (s2.std(axis=1, keepdims=True) + 1e-9)
        hh = s1 + alpha * s2
        h[:, start : start + l] += hh - hh.mean(axis=1, keepdims=True)
    return h


def skin_mask_blocks(frames_rgb: np.ndarray, grid: tuple[int, int], block: int) -> np.ndarray:
    """Boolean[n_blocks]: blocks whose mean colour falls in the YCrCb skin locus."""
    mean_img = frames_rgb[:: max(1, len(frames_rgb) // 30)].mean(axis=0).astype(np.uint8)
    ycrcb = cv2.cvtColor(mean_img, cv2.COLOR_RGB2YCrCb).astype(np.float32)
    gh, gw = grid
    y = ycrcb[: gh * block, : gw * block].reshape(gh, block, gw, block, 3).mean(axis=(1, 3)).reshape(-1, 3)
    cr, cb = y[:, 1], y[:, 2]
    return (cr > 133) & (cr < 180) & (cb > 77) & (cb < 135) & (y[:, 0] > 40)


def estimate_pulse(
    frames_rgb: np.ndarray,
    fs: float,
    block_px: int = 16,
    band: tuple[float, float] = sig.PULSE_BAND,
    win_s: float = 15.0,
    step_s: float = 5.0,
    top_k: int = 8,
) -> PulseResult:
    if fs < 2 * band[1] + 0.5:
        return PulseResult(None, 0.0, float("-inf"), 0.0)
    rgb, grid = block_means(frames_rgb.astype(np.float32), block_px)  # [n, T, 3]
    skin = skin_mask_blocks(frames_rgb, grid, block_px)
    cand = np.flatnonzero(skin) if skin.sum() >= 2 else np.arange(rgb.shape[0])
    h = pos(rgb[cand] + 1.0, fs)
    motion = gross_motion_mask(to_gray(frames_rgb), fs)
    h = sig.bandpass(h, fs, band)
    snr = sig.band_snr(h, fs, band)
    order_local = np.argsort(snr)[::-1][: min(top_k, len(cand))]
    sel = h[order_local]
    sel = sel / (sel.std(axis=1, keepdims=True) + 1e-9)
    pulse = sel.mean(axis=0)  # POS output has consistent polarity across skin blocks
    pulse[motion] = 0.0

    windows = []
    for centre, pk in sig.sliding_rate(pulse, fs, band, win_s, step_s):
        windows.append({"t": round(centre, 2), "bpm": round(pk.rate_per_min, 1), "quality": round(pk.quality, 3)})
    overall = sig.spectral_peak(pulse, fs, band)
    good = [w["bpm"] for w in windows if w["quality"] >= 0.6]
    rate = float(np.median(good)) if good else (overall.rate_per_min if overall.quality >= 0.6 else None)
    quality = float(np.mean([w["quality"] for w in windows if w["quality"] >= 0.6])) if good else overall.quality
    return PulseResult(
        rate_bpm=None if rate is None else round(rate, 1),
        quality=round(quality, 3),
        snr_db=round(overall.snr_db, 2),
        skin_fraction=round(float(skin.mean()), 3),
        windows=windows,
        roi_blocks=[int(cand[i]) for i in order_local],
        grid=grid,
        block_px=block_px,
    )
