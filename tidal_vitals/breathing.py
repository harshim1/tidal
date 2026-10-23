"""Contactless respiration-rate estimation from ordinary video.

Physics (docs/ARCHITECTURE.md §3): breathing moves the chest/blanket surface
by a fraction of a pixel. For a textured surface the brightness at a fixed
pixel changes to first order as

    I(x, t) ≈ I(x, 0) − δ(t) · ∂I/∂x            (brightness-constancy / Taylor)

so each pixel's intensity is a *scaled copy* of the displacement δ(t), with a
gain equal to the local image gradient (positive or negative). We therefore:

1. average pixels into blocks (spatial pooling, raises SNR),
2. detrend + band-pass each block to 0.1–0.7 Hz,
3. keep the blocks whose spectrum has a clear in-band peak (automatic ROI —
   no body/face detector needed, works under blankets and in IR night vision),
4. take the first principal component across those blocks — PCA absorbs the
   unknown ± gradient signs and gives the common breathing waveform,
5. estimate the rate per sliding window with an interpolated FFT peak.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from . import signal as sig
from .blocks import gray_block_means, gross_motion_mask_rgb, to_gray

# Below this confidence Tidal says "no clear reading" instead of a number.
# Chosen on a 6-seed sweep of each synthetic scenario through H.264: every true signal
# ≥ 0.8, every empty-room / codec-starved clip ≤ 0.35.
REPORT_THRESHOLD = 0.6
# Codec update share → confidence factor ramps 0 → 1 between these. True signals measured
# ≥ 0.73 (IR night lowest); empty rooms ≤ 0.39, codec-starved ≤ 0.05.
CODEC_RAMP = (0.2, 0.7)


@dataclass
class BreathingResult:
    rate_bpm: float | None  # median over good windows; None if unreliable
    quality: float  # 0..1
    snr_db: float
    fs: float
    duration_s: float
    waveform: list[float]  # normalised breathing waveform (decimated to ≤ 10 Hz)
    waveform_fs: float
    windows: list[dict] = field(default_factory=list)  # [{t, bpm, quality}]
    low_motion_intervals: list[tuple[float, float]] = field(default_factory=list)
    gross_motion_fraction: float = 0.0
    spatial_coherence: float = 0.0
    codec_update_fraction: float = 1.0
    roi_blocks: list[int] = field(default_factory=list)
    grid: tuple[int, int] = (0, 0)
    block_px: int = 8

    def to_dict(self) -> dict:
        return asdict(self)


def _fill_held_samples(series: np.ndarray, tol: float = 1e-3) -> tuple[np.ndarray, np.ndarray]:
    """Undo codec "skip" blocks: H.264 re-sends a static macroblock unchanged for
    several frames, turning a smooth signal into a staircase (sample-and-hold
    at irregular times). We keep only the frames where a block actually
    changed and linearly interpolate between them — a better reconstruction of
    the underlying band-limited breathing signal than the held staircase.
    """
    out = series.copy()
    t = series.shape[1]
    idx = np.arange(t)
    changed = np.ones_like(series, dtype=bool)
    changed[:, 1:] = np.abs(np.diff(series, axis=1)) > tol
    for i in np.flatnonzero(changed.sum(axis=1) < t - 1):
        keep = np.flatnonzero(changed[i])
        if len(keep) >= 4:
            out[i] = np.interp(idx, keep, series[i, keep])
    return out, changed.mean(axis=1)


def _spatial_coherence(blocks: np.ndarray, grid: tuple[int, int]) -> float:
    """Fraction of ROI blocks with at least one ROI neighbour (8-connectivity).

    A chest is one contiguous surface; noise that happens to peak in-band is
    scattered at random. Real breathing scores ~0.8-1.0, noise ~0.1-0.3.
    """
    gh, gw = grid
    if len(blocks) == 0 or gw == 0:
        return 0.0
    m = np.zeros((gh + 2, gw + 2), dtype=bool)
    r, c = np.divmod(np.asarray(blocks), gw)
    m[r + 1, c + 1] = True
    neigh = sum(np.roll(np.roll(m, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
    return float((neigh[r + 1, c + 1] > 0).mean())


def _block_contrast(gray: np.ndarray, block: int) -> np.ndarray:
    """Mean gradient magnitude per block of the time-averaged grey frames — the "gain" ∂I/∂x."""
    import cv2

    mean = gray.mean(axis=0).astype(np.float32)
    gx = cv2.Sobel(mean, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(mean, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    h, w = mag.shape
    gh, gw = h // block, w // block
    return mag[: gh * block, : gw * block].reshape(gh, block, gw, block).mean(axis=(1, 3)).ravel()


def _interp_masked(x: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if not mask.any() or mask.all():
        return x
    idx = np.arange(x.shape[-1])
    good = ~mask
    out = x.copy()
    for i in range(x.shape[0]):
        out[i, mask] = np.interp(idx[mask], idx[good], x[i, good])
    return out


def estimate_breathing(
    frames_rgb: np.ndarray,
    fs: float,
    block_px: int = 8,
    band: tuple[float, float] = sig.RESP_BAND,
    win_s: float = 30.0,
    step_s: float = 5.0,
    top_fraction: float = 0.08,
    min_blocks: int = 6,
) -> BreathingResult:
    t = frames_rgb.shape[0]
    duration = t / fs
    # Grey is derived chunk by chunk: a full-clip float copy is the biggest memory cost.
    motion = gross_motion_mask_rgb(frames_rgb, fs)

    series, grid = gray_block_means(frames_rgb, block_px)  # [n_blocks, T]
    series, changed_frac = _fill_held_samples(series)
    series = _interp_masked(series, motion)
    # Illumination model I(x,t) = L(t)·R(x,t): divide out the global light level.
    # Work in relative units, estimate L(t) as the per-frame *median* across
    # blocks (robust while the moving chest covers < half the image), remove it.
    series = series / (series.mean(axis=1, keepdims=True) + 1e-6) - 1.0
    series = series - np.median(series, axis=0, keepdims=True)
    # Blocks on flat, untextured surfaces carry no motion information (gain ∂I/∂x ≈ 0);
    # weight them down via their spatial contrast so noise-only blocks can't win the ROI.
    contrast = _block_contrast(to_gray(frames_rgb[:: max(1, t // 20)]), block_px)
    series = sig.detrend_smoothness_priors(series, lam=max(50.0, 20.0 * fs))
    filt = sig.bandpass(series, fs, band)

    # Automatic ROI: blocks with the strongest, cleanest in-band peak.
    snr = sig.band_snr(filt, fs, band)
    energy = filt.std(axis=1)
    score = (
        snr
        + 3.0 * np.log10(energy / (np.median(energy) + 1e-9) + 1e-9)
        + 2.0 * np.log10(contrast / (np.median(contrast) + 1e-9) + 1e-9)
    )
    k = max(min_blocks, int(top_fraction * len(score)))
    order = np.argsort(score)[::-1][:k]
    sel = filt[order]
    sel = sel / (sel.std(axis=1, keepdims=True) + 1e-9)

    # First principal component = shared breathing waveform (sign-agnostic).
    u, s, vt = np.linalg.svd(sel, full_matrices=False)
    wave = vt[0] * s[0]
    wave = wave / (wave.std() + 1e-9)

    # Second pass: the true ROI is every block that moves *with* that waveform.
    # This removes candidate blocks that only had a noise peak in-band and
    # recovers chest blocks that scored just below the cut.
    norm_all = filt / (filt.std(axis=1, keepdims=True) + 1e-9)
    corr = norm_all @ wave / len(wave)
    coherent = np.flatnonzero(np.abs(corr) >= 0.6)
    if len(coherent) >= min_blocks:
        order = coherent[np.argsort(np.abs(corr[coherent]))[::-1]]
        sel = norm_all[order] * np.sign(corr[order])[:, None]  # align polarity
        wave = sel.mean(axis=0)
        wave = wave / (wave.std() + 1e-9)
    # Orient so that the dominant block correlates positively.
    if np.dot(wave, norm_all[order[0]]) < 0:
        wave = -wave

    overall = sig.spectral_peak(wave[~motion] if (~motion).sum() > fs * 10 else wave, fs, band)

    windows = []
    for centre, pk in sig.sliding_rate(wave, fs, band, win_s, step_s):
        lo, hi = int((centre - win_s / 2) * fs), int((centre + win_s / 2) * fs)
        moved = motion[max(0, lo) : max(0, hi)].mean() if hi > lo else 0.0
        q = pk.quality * (1.0 - moved)
        windows.append({"t": round(centre, 2), "bpm": round(pk.rate_per_min, 2), "quality": round(q, 3)})

    # ---- confidence: is there really one person breathing in view? -------------
    good = [w for w in windows if w["quality"] >= 0.6]
    cands = [w["bpm"] for w in good] or ([overall.rate_per_min] if overall.quality >= 0.6 else [])
    spatial = _spatial_coherence(order, grid)
    codec = float(np.median(changed_frac[order]))  # share of frames the codec actually updated
    if cands:
        rate = float(np.median(cands))
        agree = float(np.mean([abs(w["bpm"] - rate) <= 2.0 for w in windows])) if windows else 1.0
        base = float(np.mean([w["quality"] for w in good])) if good else overall.quality
        quality = (
            base
            * (0.4 + 0.6 * agree)  # rate stable across windows
            * float(np.clip((spatial - 0.3) / 0.4, 0.0, 1.0))  # one contiguous moving surface
            # Codec-starved footage is untrustworthy: blocks the encoder holds and refreshes
            # together share a fake in-band rhythm. A ramp, not a step — a cliff at one value
            # let empty rooms just above it report a rate.
            * float(np.clip((codec - CODEC_RAMP[0]) / (CODEC_RAMP[1] - CODEC_RAMP[0]), 0.0, 1.0))
        )
    else:
        rate, quality = None, overall.quality * 0.5
    if quality < REPORT_THRESHOLD:
        rate = None

    env = sig.amplitude_envelope(wave, fs)
    env[motion] = np.median(env)  # gross motion is not "stillness"
    pauses = sig.low_amplitude_intervals(env, fs, rel_thresh=0.3, min_dur_s=8.0)

    dec = max(1, int(round(fs / 10)))
    return BreathingResult(
        rate_bpm=None if rate is None else round(rate, 2),
        quality=round(float(quality), 3),
        snr_db=round(overall.snr_db, 2),
        fs=fs,
        duration_s=round(duration, 2),
        waveform=[round(float(v), 3) for v in wave[::dec]],
        waveform_fs=fs / dec,
        windows=windows,
        low_motion_intervals=[(round(a, 1), round(b, 1)) for a, b in pauses],
        gross_motion_fraction=round(float(motion.mean()), 3),
        roi_blocks=[int(i) for i in order],
        spatial_coherence=round(spatial, 3),
        codec_update_fraction=round(codec, 3),
        grid=grid,
        block_px=block_px,
    )
