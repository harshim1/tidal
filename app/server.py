"""Tidal API server.

Routes
    POST /webhooks/ring          Ring event webhook (HMAC-verified, de-duplicated)
    POST /api/checkin/{device}   trigger a live check-in (WHEP) or clip analysis now
    POST /api/analyze            analyse an uploaded video (multipart field "file")
    GET  /api/latest             latest reading (with waveform)
    GET  /api/readings?hours=12  readings in a window
    GET  /api/reading/{id}       one reading
    GET  /api/night?hours=12     aggregated night + spoken summary
    GET  /api/cameras            Ring cameras on the linked account
    GET  /media/...              magnified renders
    GET  /                       caregiver dashboard
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from ring_sandbox import SignatureError, webhooks

from . import pipeline, ring, store, summary
from .config import ROOT, cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tidal.server")

# Ring event types that indicate someone in view — the moment to take a reading.
TRIGGER_EVENTS = {"motion_detected", "motion", "ding", "doorbell_pressed"}


def _in_night_window(now: datetime) -> bool:
    h = now.astimezone().hour
    s, e = cfg.night_start_hour, cfg.night_end_hour
    return (h >= s or h < e) if s > e else (s <= h < e)


async def _checkin_loop() -> None:
    """Scheduled live check-ins during the night window (needs real cameras)."""
    while True:
        await asyncio.sleep(cfg.checkin_minutes * 60)
        if not _in_night_window(datetime.now(UTC)):
            continue
        try:
            devices = cfg.watch_devices or [c["id"] for c in await asyncio.to_thread(ring.cameras)]
        except Exception as exc:  # noqa: BLE001
            log.warning("check-in: cannot list cameras: %s", exc)
            continue
        for dev in devices:
            await _live_checkin(dev)


async def _live_checkin(device_id: str) -> dict | None:
    try:
        frames, fps = await ring.sample_live(device_id, cfg.checkin_seconds)
    except Exception as exc:  # noqa: BLE001
        log.warning("live check-in failed for %s: %s", device_id, exc)
        return None
    started = datetime.now(UTC) - timedelta(seconds=len(frames) / fps)
    return await asyncio.to_thread(
        pipeline.process_frames, frames, fps,
        device_id=device_id, device_name=ring.device_name(device_id), source="ring_live", started_at=started,
    )


def _clip_job(device_id: str, occurred_at: datetime) -> None:
    start = occurred_at + timedelta(seconds=cfg.settle_s)
    try:
        data, actual_ms = ring.fetch_clip(device_id, start, cfg.clip_ms)
        pipeline.process_clip_bytes(
            data, device_id=device_id, device_name=ring.device_name(device_id), source="ring_clip", started_at=start
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("clip analysis failed for %s: %s", device_id, exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init()
    task = asyncio.create_task(_checkin_loop()) if cfg.checkin_minutes > 0 else None
    log.info("Tidal ready — Ring API %s, watching %s", cfg.ring_base_url, cfg.watch_devices or "all cameras")
    yield
    if task:
        task.cancel()


app = FastAPI(title="Tidal", version="0.1.0", lifespan=lifespan)
(cfg.media_dir / "renders").mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=cfg.media_dir), name="media")
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


@app.post("/webhooks/ring")
async def ring_webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
    raw = await request.body()  # verify over the raw bytes — never re-serialised JSON
    try:
        event = webhooks.parse(
            raw, signing_key=cfg.ring_hmac_key, signature=request.headers.get(webhooks.SIGNATURE_HEADER),
            max_age_s=600,
        )
    except SignatureError as exc:
        log.warning("rejected webhook: %s", exc)
        raise HTTPException(status_code=401, detail="invalid signature") from exc
    if store.seen_webhook(event.request_id):
        return JSONResponse({"ok": True, "duplicate": True})
    accepted = event.event_type in TRIGGER_EVENTS and ring.watched(event.device_id)
    if accepted:
        background.add_task(_clip_job, event.device_id, event.occurred_at)
    log.info("webhook %s %s/%s device=%s accepted=%s", event.request_id, event.event_type, event.sub_type,
             event.device_id, accepted)
    return JSONResponse({"ok": True, "accepted": accepted})


@app.post("/api/checkin/{device_id}")
async def checkin(device_id: str, mode: str = "live", background: BackgroundTasks = None) -> dict:
    if mode == "clip":
        background.add_task(_clip_job, device_id, datetime.now(UTC) - timedelta(milliseconds=cfg.clip_ms))
        return {"ok": True, "mode": "clip", "queued": True}
    reading = await _live_checkin(device_id)
    if reading is None:
        raise HTTPException(status_code=502, detail="live check-in failed (is this a real, online camera?)")
    return {"ok": True, "mode": "live", "reading": summary_view(reading)}


@app.post("/api/analyze")
async def analyze_upload(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "video too large (200 MB max)")
    try:
        reading = await asyncio.to_thread(
            pipeline.process_clip_bytes, data, device_id="upload", device_name=file.filename or "Uploaded video",
            source="upload",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "reading": store.get(reading["id"])}


def summary_view(r: dict) -> dict:
    r = dict(r)
    r.pop("waveform", None)
    return r


@app.get("/api/latest")
def latest(tz: str | None = None) -> dict:
    r = store.latest(full=True)
    return {"reading": r, "speech": summary.latest_speech(r, tz), "person": cfg.person_name}


@app.get("/api/readings")
def readings(hours: float = 12.0) -> dict:
    return {"readings": store.since(hours)}


@app.get("/api/reading/{reading_id}")
def reading(reading_id: int) -> dict:
    r = store.get(reading_id)
    if not r:
        raise HTTPException(404, "no such reading")
    return {"reading": r}


@app.get("/api/night")
def night(hours: float = 12.0, tz: str | None = None) -> dict:
    return summary.night_summary(hours, tz)


@app.get("/api/cameras")
def list_cameras() -> dict:
    try:
        return {"cameras": ring.cameras()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Ring API error: {exc}") from exc


@app.get("/healthz")
def health() -> dict:
    return {"ok": True}


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(ROOT / "app" / "static" / "index.html")
