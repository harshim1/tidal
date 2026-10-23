"""Video I/O helpers: decode to RGB float frames, encode browser-playable MP4."""

from __future__ import annotations

import itertools
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np


def read_video(
    path: str | Path,
    max_side: int = 320,
    max_seconds: float | None = None,
    start_seconds: float = 0.0,
) -> tuple[np.ndarray, float]:
    """Decode ``path`` → (frames[T,H,W,3] uint8 RGB, fps).

    Frames are down-scaled so the longer side is ``max_side`` — vital-sign
    signals are spatially smooth, and 320 px keeps a 60 s clip under ~200 MB.
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if not (1.0 <= fps <= 240.0):
        fps = 15.0  # Ring clips occasionally report 0/garbage; 15 fps is Ring's common rate
    if start_seconds > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, start_seconds * 1000)
    limit = int(max_seconds * fps) if max_seconds else None
    # Fill a preallocated array (list + np.stack would briefly hold the clip twice);
    # the container's frame count is only a hint, so grow if it was low.
    cap_n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    est = max(1, min(cap_n, limit) if limit and cap_n > 0 else cap_n or limit or 256)
    out: np.ndarray | None = None
    n = 0
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        h, w = bgr.shape[:2]
        scale = max_side / max(h, w)
        if scale < 1.0:
            bgr = cv2.resize(bgr, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        if out is None:
            out = np.empty((est,) + rgb.shape, np.uint8)
        elif n == len(out):
            out = np.concatenate([out, np.empty_like(out)])
        out[n] = rgb
        n += 1
        if limit and n >= limit:
            break
    cap.release()
    if out is None:
        raise ValueError(f"no decodable frames in {path}")
    return out[:n], float(fps)


def write_video(frames_rgb: Iterable[np.ndarray], fps: float, out_path: str | Path, crf: int = 20) -> Path:
    """Write frames as H.264 MP4 (browser-playable) when ffmpeg is present.

    ``frames_rgb`` may be a generator, so long renders never hold every frame in
    memory. Falls back to OpenCV's MPEG-4 Part 2 encoder, which plays in VLC but
    not in most browsers (generators can't fall back: they are already consumed).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seq = frames_rgb if isinstance(frames_rgb, (list, tuple, np.ndarray)) else None
    it = iter(frames_rgb)
    first = next(it)
    frames_rgb = itertools.chain([first], it)
    h, w = first.shape[:2]
    # libx264 + yuv420p needs even dimensions.
    h2, w2 = h - h % 2, w - w % 2
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w2}x{h2}", "-r", f"{fps:.4f}",
            "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(out_path),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        assert proc.stdin is not None
        for f in frames_rgb:
            proc.stdin.write(np.ascontiguousarray(f[:h2, :w2]).astype(np.uint8).tobytes())
        proc.stdin.close()
        if proc.wait() == 0:
            return out_path
        if seq is None:
            raise RuntimeError("ffmpeg failed while encoding a streamed video")
        frames_rgb = seq
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w2, h2))
    for f in frames_rgb:
        writer.write(cv2.cvtColor(np.ascontiguousarray(f[:h2, :w2]).astype(np.uint8), cv2.COLOR_RGB2BGR))
    writer.release()
    return out_path


def bytes_to_tempfile(data: bytes, suffix: str = ".mp4") -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.close()
    return Path(tmp.name)
