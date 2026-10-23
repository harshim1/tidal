"""One-call analysis: video → breathing + pulse + status."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .breathing import BreathingResult, estimate_breathing
from .rppg import PulseResult, estimate_pulse
from .video import read_video

# Adult resting respiration reference range used for *awareness* colouring only.
# Not a diagnostic threshold. (Typical adult resting rate 12–20 /min.)
RESP_TYPICAL = (10.0, 22.0)


@dataclass
class VitalsReport:
    breathing: BreathingResult
    pulse: PulseResult | None
    status: str  # "steady" | "attention" | "restless" | "no_signal"
    status_reason: str
    fps: float
    frames: int

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _status(b: BreathingResult) -> tuple[str, str]:
    if b.gross_motion_fraction > 0.5:
        return "restless", "Lots of movement — breathing could not be measured reliably."
    if b.rate_bpm is None:
        return "no_signal", "No clear breathing rhythm in view (person out of frame, or covered too thickly)."
    if b.low_motion_intervals:
        longest = max(e - s for s, e in b.low_motion_intervals)
        return "attention", f"Breathing motion went quiet for about {longest:.0f} seconds."
    lo, hi = RESP_TYPICAL
    if not (lo <= b.rate_bpm <= hi):
        return "attention", f"Breathing rate {b.rate_bpm:.0f}/min is outside the usual resting range."
    return "steady", "Breathing is regular."


def analyze_frames(frames_rgb: np.ndarray, fs: float, pulse: bool = True) -> VitalsReport:
    b = estimate_breathing(frames_rgb, fs)
    p = estimate_pulse(frames_rgb, fs) if pulse else None
    status, reason = _status(b)
    return VitalsReport(b, p, status, reason, fs, int(frames_rgb.shape[0]))


def analyze(path: str | Path, max_seconds: float | None = 180.0, pulse: bool = True) -> VitalsReport:
    frames, fs = read_video(path, max_seconds=max_seconds)
    return analyze_frames(frames, fs, pulse=pulse)
