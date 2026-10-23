"""Ring Partner API access: device discovery, clip download, live WHEP sampling.

Uses the typed ``ring_sandbox.RingClient`` over the Ring (Amazon Vision) REST
API. Pointing ``RING_BASE_URL`` at the ring-sandbox emulator runs the same
code path offline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from functools import lru_cache

import cv2
import numpy as np
from ring_sandbox import RingClient

from .config import cfg

log = logging.getLogger("tidal.ring")


def client() -> RingClient:
    return RingClient(cfg.ring_token, base_url=cfg.ring_base_url, media_origins=cfg.media_origins)


@lru_cache(maxsize=64)
def device_name(device_id: str) -> str:
    try:
        with client() as ring:
            return ring.device(device_id).name or device_id
    except Exception:  # noqa: BLE001
        return device_id


def cameras() -> list[dict]:
    with client() as ring:
        out = []
        for d in ring.devices(include=["status", "capabilities"]):
            caps = getattr(d, "capabilities", None)
            if caps is None or getattr(caps, "is_camera", True):
                out.append({"id": d.id, "name": d.name, "online": getattr(d, "online", None)})
        return out


def watched(device_id: str) -> bool:
    return not cfg.watch_devices or device_id in cfg.watch_devices


def fetch_clip(device_id: str, start: datetime, duration_ms: int) -> tuple[bytes, int]:
    """Download the recorded clip starting at ``start``. Returns (mp4 bytes, actual_ms)."""
    with client() as ring:
        clip = ring.clip(device_id, timestamp=start, duration_ms=duration_ms)
    actual = clip.actual_length_ms or duration_ms
    if clip.partial:
        log.info("partial clip: %s of %s ms", actual, duration_ms)
    return clip.content, actual


async def sample_live(device_id: str, seconds: float = 60.0, max_side: int = 320) -> tuple[np.ndarray, float]:
    """Record ``seconds`` of live video over WHEP (WebRTC) → (frames RGB, fps).

    Requires a real Ring camera (the emulator negotiates SDP but sends no media).
    Frame timestamps come from RTP presentation times, so the returned fps is
    the *measured* rate — Ring live video is variable-rate.
    """
    from aiortc import RTCPeerConnection, RTCSessionDescription

    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    frames: list[np.ndarray] = []
    stamps: list[float] = []
    done = asyncio.Event()

    @pc.on("track")
    def on_track(track):  # noqa: ANN001
        async def reader() -> None:
            t0 = None
            try:
                while True:
                    fr = await track.recv()
                    ts = float(fr.time) if fr.time is not None else time.monotonic()
                    t0 = ts if t0 is None else t0
                    img = fr.to_ndarray(format="rgb24")
                    h, w = img.shape[:2]
                    s = max_side / max(h, w)
                    if s < 1:
                        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
                    frames.append(img)
                    stamps.append(ts - t0)
                    if ts - t0 >= seconds:
                        break
            finally:
                done.set()

        asyncio.ensure_future(reader())

    session = None
    ring = client()
    try:
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.05)
        session = await asyncio.to_thread(ring.whep_session, device_id, pc.localDescription.sdp)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=session.sdp_answer, type="answer"))
        await asyncio.wait_for(done.wait(), timeout=seconds + 30)
    finally:
        await pc.close()
        if session is not None:
            try:
                await asyncio.to_thread(ring.whep_close, session)
            except Exception:  # noqa: BLE001
                pass
        ring.close()
    if len(frames) < 10:
        raise RuntimeError("live stream produced too few frames")
    st = np.asarray(stamps)
    fps = (len(st) - 1) / (st[-1] - st[0]) if st[-1] > st[0] else 15.0
    # Resample onto a uniform grid (nearest frame) so FFT-based estimators see constant fs.
    grid = np.arange(0, st[-1], 1.0 / fps)
    idx = np.clip(np.searchsorted(st, grid), 0, len(frames) - 1)
    return np.stack([frames[i] for i in idx]), float(fps)
