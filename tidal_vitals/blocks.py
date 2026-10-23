"""Spatial block averaging and gross-motion detection.

Averaging pixels inside an ``b×b`` block raises SNR by ~sqrt(b²) for spatially
correlated signals (pulse colour, breathing-induced shading) while uncorrelated
sensor noise averages out. It is the cheapest form of the spatial pooling
step in EVM / rPPG pipelines.
"""

from __future__ import annotations

import numpy as np


def block_means(frames: np.ndarray, block: int) -> tuple[np.ndarray, tuple[int, int]]:
    """frames[T,H,W] or [T,H,W,C] → series[n_blocks, T(, C)], grid (gh, gw)."""
    t, h, w = frames.shape[:3]
    gh, gw = h // block, w // block
    f = frames[:, : gh * block, : gw * block].astype(np.float32)
    if f.ndim == 3:
        f = f.reshape(t, gh, block, gw, block).mean(axis=(2, 4))
        return f.reshape(t, gh * gw).T, (gh, gw)
    c = f.shape[-1]
    f = f.reshape(t, gh, block, gw, block, c).mean(axis=(2, 4))
    return f.reshape(t, gh * gw, c).transpose(1, 0, 2), (gh, gw)


def to_gray(frames_rgb: np.ndarray) -> np.ndarray:
    f = frames_rgb.astype(np.float32)
    return 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]


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
    t = gray.shape[0]
    lag = max(1, int(round(lag_s * fs)))
    if t <= lag:
        return np.zeros(t, dtype=bool)
    small = gray[:, ::2, ::2]
    k = np.ones((3, 3), np.float32) / 9.0
    import cv2  # local import keeps module import cheap

    small = np.stack([cv2.filter2D(f, -1, k) for f in small])
    frac = (np.abs(small[lag:] - small[:-lag]) > level).mean(axis=(1, 2))
    frac = np.concatenate([np.zeros(lag), frac])
    mask = frac > area
    pad = int(pad_s * fs)
    if pad and mask.any():
        idx = np.flatnonzero(mask)
        for i in idx:
            mask[max(0, i - pad - lag) : min(t, i + pad + 1)] = True
    return mask
