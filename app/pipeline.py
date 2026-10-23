"""Glue: footage (clip bytes, live frames, or upload) → analysis → stored reading."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tidal_vitals import analyze_frames, render_overlay_video
from tidal_vitals.video import bytes_to_tempfile, read_video

from . import store
from .config import cfg

log = logging.getLogger("tidal.pipeline")


def process_frames(
    frames: np.ndarray,
    fps: float,
    *,
    device_id: str,
    device_name: str,
    source: str,
    started_at: datetime | None = None,
) -> dict:
    report = analyze_frames(frames, fps)
    b, p = report.breathing, report.pulse
    video_rel = None
    if cfg.render_video:
        name = f"{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}.mp4"
        out = cfg.media_dir / "renders" / name
        try:
            render_overlay_video(frames, fps, b, out)
            video_rel = f"renders/{name}"
        except Exception as exc:  # noqa: BLE001 — a failed render must not lose the reading
            log.warning("render failed: %s", exc)
    reading = {
        "device_id": device_id,
        "device_name": device_name,
        "source": source,
        "started_at": (started_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "duration_s": b.duration_s,
        "resp_bpm": b.rate_bpm,
        "resp_quality": b.quality,
        "pulse_bpm": p.rate_bpm if p else None,
        "pulse_quality": p.quality if p else None,
        "status": report.status,
        "status_reason": report.status_reason,
        "pauses": b.low_motion_intervals,
        "motion": b.gross_motion_fraction,
        "windows": b.windows,
        "waveform": b.waveform,
        "waveform_fs": b.waveform_fs,
        "video": video_rel,
    }
    reading["id"] = store.add_reading(reading)
    log.info(
        "reading %s from %s: resp=%s q=%.2f status=%s",
        reading["id"], device_name, b.rate_bpm, b.quality, report.status,
    )
    return reading


def process_video_file(path: str | Path, max_seconds: float = 180.0, **meta) -> dict:
    frames, fps = read_video(path, max_seconds=max_seconds)
    return process_frames(frames, fps, **meta)


def process_clip_bytes(data: bytes, **meta) -> dict:
    tmp = bytes_to_tempfile(data)
    try:
        return process_video_file(tmp, **meta)
    finally:
        tmp.unlink(missing_ok=True)
