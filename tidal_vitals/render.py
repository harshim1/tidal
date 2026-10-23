"""Demo render: original | magnified side by side, with a live breathing trace.

This is the "wow" shot for the demo video: the left panel looks frozen, the
right panel shows the chest rising and falling, and the trace underneath is
the signal the estimator actually measured.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .breathing import BreathingResult
from .evm import MotionMagnifier
from .video import write_video

INK = (236, 240, 245)
DIM = (140, 152, 168)
TIDE = (94, 203, 214)  # teal
WARN = (245, 176, 92)
BG = (11, 17, 26)


def _label(img: np.ndarray, text: str, org: tuple[int, int], scale: float = 0.55, color=INK, thick: int = 1) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def render_overlay_video(
    frames_rgb: np.ndarray,
    fs: float,
    breathing: BreathingResult,
    out_path: str | Path,
    alpha: float = 30.0,
    lambda_c: float = 40.0,
    band: tuple[float, float] = (0.15, 0.6),
    panel_w: int = 480,
    show_roi: bool = True,
) -> Path:
    t_total, h, w = frames_rgb.shape[:3]
    scale = panel_w / w
    ph = int(round(h * scale))
    strip_h = 130
    out_w, out_h = panel_w * 2 + 12, ph + strip_h + 44

    wave = np.asarray(breathing.waveform, dtype=float)
    wfs = breathing.waveform_fs
    span_s = 30.0

    # ROI overlay mask (blocks chosen by the estimator)
    roi = np.zeros((h, w), np.uint8)
    gh, gw = breathing.grid
    bp = breathing.block_px
    for idx in breathing.roi_blocks:
        r, c = divmod(idx, gw) if gw else (0, 0)
        roi[r * bp : (r + 1) * bp, c * bp : (c + 1) * bp] = 255
    roi = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, np.ones((bp * 2 + 1, bp * 2 + 1), np.uint8))
    roi = cv2.morphologyEx(roi, cv2.MORPH_OPEN, np.ones((bp + 1, bp + 1), np.uint8))
    roi_big = cv2.resize(roi, (panel_w, ph), interpolation=cv2.INTER_NEAREST)
    roi_big = (cv2.GaussianBlur(roi_big, (0, 0), 6) > 127).astype(np.uint8) * 255
    contours, _ = cv2.findContours(roi_big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    mag = MotionMagnifier(fs, band=band, alpha=alpha, lambda_c=lambda_c)
    win_lookup = breathing.windows

    def _canvas(i: int) -> np.ndarray:
        t = i / fs
        m = mag.process(frames_rgb[i])
        canvas = np.full((out_h, out_w, 3), BG, np.uint8)
        left = cv2.resize(frames_rgb[i], (panel_w, ph), interpolation=cv2.INTER_LINEAR)
        right = cv2.resize(m, (panel_w, ph), interpolation=cv2.INTER_LINEAR)
        if show_roi and contours:
            cv2.drawContours(right, contours, -1, TIDE, 1, cv2.LINE_AA)
        canvas[44 : 44 + ph, :panel_w] = left
        canvas[44 : 44 + ph, panel_w + 12 :] = right
        _label(canvas, "WHAT THE CAMERA SEES", (10, 28), 0.55, DIM)
        _label(canvas, "WHAT TIDAL SEES  (sub-pixel motion, magnified)", (panel_w + 22, 28), 0.55, TIDE)

        # breathing trace
        y0 = 44 + ph + 10
        sh = strip_h - 20
        cv2.rectangle(canvas, (10, y0), (out_w - 10, y0 + sh), (22, 30, 42), -1)
        cur = int(t * wfs)
        n_span = int(span_s * wfs)
        lo = max(0, cur - n_span + 1)
        seg = wave[lo : cur + 1][-n_span:]
        if len(seg) > 1:
            norm = np.clip(seg / 3.0, -1, 1)
            xs = np.linspace(10, out_w - 230, int(span_s * wfs))[-len(seg) :]
            ys = y0 + sh / 2 - norm * (sh / 2 - 8)
            pts = np.stack([xs, ys], axis=1).astype(np.int32)
            cv2.polylines(canvas, [pts], False, TIDE, 2, cv2.LINE_AA)
            cv2.circle(canvas, tuple(pts[-1]), 4, INK, -1, cv2.LINE_AA)

        bpm = None
        for wnd in win_lookup:
            if wnd["t"] <= t + 1e-6 and wnd["quality"] >= 0.5:
                bpm = wnd["bpm"]
        in_pause = any(a <= t <= b for a, b in breathing.low_motion_intervals)
        if in_pause:
            _label(canvas, "STILL", (out_w - 205, y0 + 48), 1.0, WARN, 2)
            _label(canvas, "breathing motion paused", (out_w - 215, y0 + 78), 0.45, WARN)
        else:
            txt = f"{bpm:.0f}" if bpm else "--"
            _label(canvas, txt, (out_w - 205, y0 + 62), 1.6, INK, 3)
            _label(canvas, "breaths / min", (out_w - 205, y0 + 88), 0.5, DIM)
        _label(canvas, f"{t:5.1f}s", (out_w - 80, y0 + sh - 8), 0.45, DIM)
        return canvas

    # Stream canvases to the encoder: holding ~1.5 MB per frame costs GBs on long clips.
    return write_video((_canvas(i) for i in range(t_total)), fs, out_path, crf=20)
