"""tidal-vitals — contactless vital signs from ordinary video.

Public API:
    analyze(path) / analyze_frames(frames, fs)   breathing + pulse in one call
    estimate_breathing, estimate_pulse          individual estimators
    MotionMagnifier, magnify                    Eulerian video magnification
    render_overlay_video                        side-by-side demo render
"""

from .analyze import VitalsReport, analyze, analyze_frames
from .breathing import BreathingResult, estimate_breathing
from .evm import MotionMagnifier, magnify
from .render import render_overlay_video
from .rppg import PulseResult, estimate_pulse

__all__ = [
    "BreathingResult",
    "MotionMagnifier",
    "PulseResult",
    "VitalsReport",
    "analyze",
    "analyze_frames",
    "estimate_breathing",
    "estimate_pulse",
    "magnify",
    "render_overlay_video",
]
__version__ = "0.1.0"
