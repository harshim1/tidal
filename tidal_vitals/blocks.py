"""Spatial block averaging and gross-motion detection.

Averaging pixels inside an ``b×b`` block raises SNR by ~sqrt(b²) for spatially
correlated signals (pulse colour, breathing-induced shading) while uncorrelated
sensor noise averages out. It is the cheapest form of the spatial pooling
step in EVM / rPPG pipelines.
"""

from __future__ import annotations

import numpy as np

# Frames converted to float32 at once — bounds peak memory on long clips.
CHUNK = 64


def block_means(frames: np.ndarray, block: int) -> tuple[np.ndarray, tuple[int, int]]:
    """frames[T,H,W] or [T,H,W,C] → series[n_blocks, T(, C)], grid (gh, gw)."""
    t, h, w = frames.shape[:3]
    gh, gw = h // block, w // block
    shape = (gh, block, gw, block) + frames.shape[3:]
    out = np.empty((t, gh, gw) + frames.shape[3:], np.float32)
    for s in range(0, t, CHUNK):  # float32 copies a chunk at a time, not the whole clip
        f = frames[s : s + CHUNK, : gh * block, : gw * block].astype(np.float32)
        out[s : s + CHUNK] = f.reshape((len(f),) + shape).mean(axis=(2, 4))
    if frames.ndim == 3:
        return out.reshape(t, gh * gw).T, (gh, gw)
    return out.reshape(t, gh * gw, frames.shape[-1]).transpose(1, 0, 2), (gh, gw)


def to_gray(frames_rgb: np.ndarray) -> np.ndarray:
    out = np.empty(frames_rgb.shape[:3], np.float32)
    for s in range(0, len(frames_rgb), CHUNK):
        f = frames_rgb[s : s + CHUNK].astype(np.float32)
        out[s : s + CHUNK] = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    return out


def gray_block_means(frames_rgb: np.ndarray, block: int) -> tuple[np.ndarray, tuple[int, int]]:
    """``block_means(to_gray(frames_rgb), block)`` without a full-clip grey copy."""
    parts = [block_means(to_gray(frames_rgb[s : s + CHUNK]), block) for s in range(0, len(frames_rgb), CHUNK)]
    return np.concatenate([p for p, _ in parts], axis=1), parts[0][1]


def gross_motion_mask_rgb(frames_rgb: np.ndarray, fs: float, **kwargs) -> np.ndarray:
    """``gross_motion_mask(to_gray(frames_rgb), fs)``, converting only the half-res pixels it reads."""
    return _motion_mask(to_gray(frames_rgb[:, ::2, ::2]), fs, **kwargs)


def gross_motion_mask(
    gray: np.ndarray, fs: float, level: float = 8.0, area: float = 0.02, lag_s: float = 0.5, pad_s: float = 2.0
) -> np.ndarray:
    """Boolean[T]: True where the scene has large motion (rolling over, walking).

    Compares each frame with the one ``lag_s`` earlier (after a light blur to
    suppress sensor noise and codec block artefacts) and flags frames where more
    than ``area`` of the image changed by more than ``level`` grey levels.
    Breathing moves the image by a fraction of a pixel and changes pixels by a
    few grey levels at most; a person turning over changes thousands of
    pixels by tens of levels. Codec key-frames change many pixels by ~1 level
    and are ignored.
    """
    return _motion_mask(gray[:, ::2, ::2], fs, level, area, lag_s, pad_s)


def _motion_mask(
    small: np.ndarray, fs: float, level: float = 8.0, area: float = 0.02, lag_s: float = 0.5, pad_s: float = 2.0
) -> np.ndarray:
    """gross_motion_mask on an already half-resolution grey clip."""
    t = small.shape[0]
    lag = max(1, int(round(lag_s * fs)))
    if t <= lag:
        return np.zeros(t, dtype=bool)
    k = np.ones((3, 3), np.float32) / 9.0
    import cv2  # local import keeps module import cheap

    blur = np.empty_like(small)
    for i, f in enumerate(small):
        cv2.filter2D(f, -1, k, dst=blur[i])
    # Per frame, not one full-clip difference array: keeps peak memory near one clip copy.
    frac = np.array([(np.abs(blur[i] - blur[i - lag]) > level).mean() for i in range(lag, t)])
    frac = np.concatenate([np.zeros(lag), frac])
    mask = frac > area
    pad = int(pad_s * fs)
    if pad and mask.any():
        idx = np.flatnonzero(mask)
        for i in idx:
            mask[max(0, i - pad - lag) : min(t, i + pad + 1)] = True
    return mask
