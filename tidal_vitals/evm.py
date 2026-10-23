"""Eulerian Video Magnification (Wu, Rubinstein, Shih, Guttag, Durand, Freeman —
SIGGRAPH 2012), linear motion-magnification variant.

Model: a 1-D signal I(x, t) = f(x + δ(t)). First-order Taylor expansion:

    I(x, t) ≈ f(x) + δ(t) ∂f/∂x
    B(x, t) = temporal band-pass of I  ≈ δ(t) ∂f/∂x          (only the motion term survives)
    Ĩ(x, t) = I + α B ≈ f(x + (1 + α) δ(t))                    (motion amplified by 1+α)

The approximation holds while (1 + α) δ(t) < λ / 8, where λ is the spatial
wavelength. So we decompose each frame with a Laplacian pyramid and attenuate
α on fine levels (small λ) — the paper's λ_c cutoff — to avoid artefacts.

Temporal filtering uses the paper's real-time IIR form: the difference of two
first-order low-pass filters, which is a band-pass with ~zero memory cost.
Processing is streaming: one frame in, one magnified frame out.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

_RGB2YIQ = np.array(
    [[0.299, 0.587, 0.114], [0.595716, -0.274453, -0.321263], [0.211456, -0.522591, 0.311135]],
    dtype=np.float32,
)
_YIQ2RGB = np.linalg.inv(_RGB2YIQ).astype(np.float32)


def _lap_pyramid(img: np.ndarray, levels: int) -> list[np.ndarray]:
    pyr, cur = [], img
    for _ in range(levels - 1):
        down = cv2.pyrDown(cur)
        up = cv2.pyrUp(down, dstsize=(cur.shape[1], cur.shape[0]))
        pyr.append(cur - up)
        cur = down
    pyr.append(cur)
    return pyr


def _collapse(pyr: list[np.ndarray]) -> np.ndarray:
    cur = pyr[-1]
    for lap in reversed(pyr[:-1]):
        cur = cv2.pyrUp(cur, dstsize=(lap.shape[1], lap.shape[0])) + lap
    return cur


class MotionMagnifier:
    """Streaming linear EVM. Feed RGB uint8 frames, get magnified RGB uint8 frames."""

    def __init__(
        self,
        fs: float,
        band: tuple[float, float] = (0.15, 0.6),
        alpha: float = 20.0,
        lambda_c: float = 60.0,
        chrom_attenuation: float = 0.1,
        levels: int | None = None,
        exaggeration: float = 2.0,
    ) -> None:
        self.fs = fs
        # First-order IIR low-pass coefficient for cutoff f: r = 1 - exp(-2πf/fs)
        self.r_hi = 1.0 - math.exp(-2 * math.pi * band[1] / fs)
        self.r_lo = 1.0 - math.exp(-2 * math.pi * band[0] / fs)
        self.alpha = alpha
        self.lambda_c = lambda_c
        self.chrom = chrom_attenuation
        self.levels = levels
        self.exaggeration = exaggeration
        self._lp1: list[np.ndarray] | None = None
        self._lp2: list[np.ndarray] | None = None
        self._alphas: list[float] = []

    def _init(self, pyr: list[np.ndarray], shape: tuple[int, int]) -> None:
        self._lp1 = [p.copy() for p in pyr]
        self._lp2 = [p.copy() for p in pyr]
        h, w = shape
        lam = math.sqrt(h * h + w * w) / 3.0  # representative wavelength of level 0
        delta = self.lambda_c / 8.0 / (1.0 + self.alpha)
        n = len(pyr)
        self._alphas = []
        for lvl in range(n):
            cur = (lam / delta / 8.0 - 1.0) * self.exaggeration
            if lvl == 0 or lvl == n - 1:
                a = 0.0  # finest level = noise, coarsest = DC: never amplify
            else:
                a = min(self.alpha, max(cur, 0.0))
            self._alphas.append(a)
            lam /= 2.0
        # Order: pyramid index 0 is finest → reverse so coarse levels get large alpha.
        self._alphas = list(reversed(self._alphas))
        self._alphas[0] = 0.0
        self._alphas[-1] = 0.0

    def process(self, frame_rgb: np.ndarray) -> np.ndarray:
        f = frame_rgb.astype(np.float32) / 255.0
        yiq = f @ _RGB2YIQ.T
        h, w = yiq.shape[:2]
        levels = self.levels or max(3, int(math.log2(min(h, w))) - 2)
        pyr = _lap_pyramid(yiq, levels)
        if self._lp1 is None:
            self._init(pyr, (h, w))
            return frame_rgb.copy()
        assert self._lp2 is not None
        out_pyr = []
        for i, p in enumerate(pyr):
            self._lp1[i] = (1 - self.r_hi) * self._lp1[i] + self.r_hi * p
            self._lp2[i] = (1 - self.r_lo) * self._lp2[i] + self.r_lo * p
            band = (self._lp1[i] - self._lp2[i]) * self._alphas[i]
            out_pyr.append(band)
        mag = _collapse(out_pyr)
        mag[..., 1:] *= self.chrom
        out = (yiq + mag) @ _YIQ2RGB.T
        return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def magnify(frames_rgb: np.ndarray, fs: float, **kwargs) -> np.ndarray:
    m = MotionMagnifier(fs, **kwargs)
    return np.stack([m.process(f) for f in frames_rgb])
