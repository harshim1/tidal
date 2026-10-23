"""Synthetic ground-truth video generator.

Renders a top-down "person asleep under a blanket" scene with *known*
physiology so every estimator can be validated objectively:

* breathing: sub-pixel vertical displacement of the torso, rate drifting
  slowly, optional breath-hold interval (``pause``),
* pulse: tiny reflectance modulation of exposed skin (face) with the
  green-dominant absorption profile of oxy-haemoglobin,
* nuisances: sensor noise, slow auto-exposure drift, an optional roll-over
  (gross motion), and H.264 compression when written to disk.

This is test data, not a substitute for recordings of real people — see
docs/ARCHITECTURE.md §7 for the real-data validation protocol.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np


@dataclass
class SynthSpec:
    width: int = 320
    height: int = 240
    fps: float = 15.0
    duration_s: float = 90.0
    resp_bpm_start: float = 14.0
    resp_bpm_end: float = 16.0
    resp_amplitude_px: float = 0.35  # peak chest displacement at the bump centre
    pulse_bpm: float = 64.0
    pulse_amplitude: float = 0.004  # fractional reflectance change (0.4 %)
    pause: tuple[float, float] | None = (52.0, 64.0)  # breath-hold interval, s
    rollover: tuple[float, float] | None = None  # gross-motion interval, s
    noise_sigma: float = 1.2
    seed: int = 7
    night_ir: bool = False  # grayscale, like Ring night vision


@dataclass
class GroundTruth:
    spec: dict
    resp_rate_bpm: list[float] = field(default_factory=list)  # per frame
    pulse_rate_bpm: float = 0.0
    pause: tuple[float, float] | None = None
    rollover: tuple[float, float] | None = None


def _texture(rng: np.random.Generator, h: int, w: int, sigma: float, scale: float) -> np.ndarray:
    n = rng.normal(0, 1, (h, w)).astype(np.float32)
    n = cv2.GaussianBlur(n, (0, 0), sigma)
    return n / (n.std() + 1e-9) * scale


def render(spec: SynthSpec) -> tuple[np.ndarray, GroundTruth]:
    rng = np.random.default_rng(spec.seed)
    h, w, fs = spec.height, spec.width, spec.fps
    n = int(spec.duration_s * fs)
    t = np.arange(n) / fs
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    # --- static scene --------------------------------------------------------------
    room = 70 + 25 * (xx / w) + _texture(rng, h, w, 6, 6)
    scene = np.stack([room * 0.95, room * 0.9, room * 0.85], axis=-1)

    # bed
    bed = (np.abs(xx - w * 0.5) < w * 0.36) & (np.abs(yy - h * 0.55) < h * 0.42)
    sheet = 185 + _texture(rng, h, w, 1.5, 7)
    for c, k in enumerate((1.0, 1.0, 0.97)):
        scene[..., c] = np.where(bed, sheet * k, scene[..., c])

    # blanket over the torso — folds give the texture that makes motion visible
    torso_c = (w * 0.5, h * 0.62)
    torso = ((xx - torso_c[0]) / (w * 0.26)) ** 2 + ((yy - torso_c[1]) / (h * 0.33)) ** 2 < 1
    folds = 120 + 18 * np.sin(xx / 5.0 + 1.7 * np.sin(yy / 13.0)) + _texture(rng, h, w, 1.2, 10)
    blanket_rgb = (folds * 0.55, folds * 0.62, folds * 0.95)
    for c in range(3):
        scene[..., c] = np.where(torso, blanket_rgb[c], scene[..., c])

    # head (skin)
    head_c = (w * 0.5, h * 0.22)
    head = ((xx - head_c[0]) / (w * 0.085)) ** 2 + ((yy - head_c[1]) / (h * 0.13)) ** 2 < 1
    skin_tex = _texture(rng, h, w, 2.0, 4)
    skin_rgb = (205 + skin_tex, 160 + skin_tex, 135 + skin_tex)
    for c in range(3):
        scene[..., c] = np.where(head, skin_rgb[c], scene[..., c])
    scene = cv2.GaussianBlur(scene.astype(np.float32), (0, 0), 0.6)

    # --- physiology ------------------------------------------------------------------
    rate = np.linspace(spec.resp_bpm_start, spec.resp_bpm_end, n)
    phase = 2 * np.pi * np.cumsum(rate / 60.0) / fs
    resp = np.sin(phase) + 0.15 * np.sin(2 * phase + 0.6)  # mild inhale/exhale asymmetry
    if spec.pause:
        a, b = spec.pause
        ramp = np.clip(np.minimum((t - a) / 1.5, (b - t) / 1.5), 0, 1)
        resp *= 1 - ramp
    # chest bump: displacement strongest over the sternum, fading to the edges
    bump = np.exp(-(((xx - torso_c[0]) / (w * 0.17)) ** 2 + ((yy - (torso_c[1] - h * 0.08)) / (h * 0.2)) ** 2))
    bump *= torso | (cv2.dilate(torso.astype(np.uint8), np.ones((5, 5))) > 0)

    pulse_phase = 2 * np.pi * np.cumsum(spec.pulse_bpm / 60.0 + 0.03 * np.sin(2 * np.pi * 0.1 * t)) / fs
    pulse = np.sin(pulse_phase) + 0.35 * np.sin(2 * pulse_phase - 1.0)  # dicrotic shape
    pulse /= np.abs(pulse).max()
    hb_profile = np.array([0.33, 0.77, 0.53], dtype=np.float32)  # R,G,B absorption weighting

    drift = 1.0 + 0.03 * np.sin(2 * np.pi * t / 47.0)

    frames = np.empty((n, h, w, 3), dtype=np.uint8)
    for i in range(n):
        dy = spec.resp_amplitude_px * resp[i] * bump
        dx = np.zeros_like(dy)
        if spec.rollover and spec.rollover[0] <= t[i] <= spec.rollover[1]:
            frac = (t[i] - spec.rollover[0]) / (spec.rollover[1] - spec.rollover[0])
            dx = dx + 12.0 * np.sin(np.pi * frac) * torso
        frame = cv2.remap(scene, (xx + dx).astype(np.float32), (yy + dy).astype(np.float32), interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        skin_mod = 1.0 - spec.pulse_amplitude * pulse[i] * hb_profile  # more blood → darker
        frame = np.where(head[..., None], frame * skin_mod, frame)
        frame = frame * drift[i] + rng.normal(0, spec.noise_sigma, frame.shape)
        if spec.night_ir:
            g = frame.mean(axis=-1, keepdims=True)
            frame = np.repeat(g, 3, axis=-1)
        frames[i] = np.clip(frame, 0, 255).astype(np.uint8)

    gt = GroundTruth(
        spec=asdict(spec),
        resp_rate_bpm=[round(float(r), 3) for r in rate],
        pulse_rate_bpm=spec.pulse_bpm,
        pause=spec.pause,
        rollover=spec.rollover,
    )
    return frames, gt


def write_dataset(out_dir: str | Path, spec: SynthSpec, name: str, crf: int = 23) -> Path:
    from .video import write_video

    out_dir = Path(out_dir)
    frames, gt = render(spec)
    path = write_video(frames, spec.fps, out_dir / f"{name}.mp4", crf=crf)
    (out_dir / f"{name}.json").write_text(json.dumps(asdict(gt), indent=1))
    return path
