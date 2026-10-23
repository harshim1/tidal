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
    GET  /api/config             what this deployment allows (demo mode, uploads)
    GET  /media/...              magnified renders
    POST /mcp                    Alexa+ MCP server (same tools as `python -m app.mcp_server`)
    GET  /                       caregiver dashboard

Access (when TIDAL_PASSWORD / TIDAL_MCP_TOKEN are set — always, with TIDAL_PUBLIC=1):
people use HTTP Basic auth with the password (any username), MCP clients send
``Authorization: Bearer <token>``; /healthz and the HMAC-signed webhook stay open.
The demo with uploads off (synthetic data only) needs no password, so judges can open it.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from ring_sandbox import SignatureError, webhooks

from . import mcp_server, pipeline, ring, store, summary
from .config import ROOT, cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tidal.server")

# Ring event types that indicate someone in view — the moment to take a reading.
TRIGGER_EVENTS = {"motion_detected", "motion", "ding", "doorbell_pressed"}

# Bundled synthetic night for TIDAL_DEMO=1 (built by scripts/build_demo_snapshot.py).
DEMO_DIR = ROOT / "demo"


def _demo_morning(now: datetime) -> datetime:
    """The latest TIDAL_DEMO_WAKE_HOUR:00 (demo zone) not after ``now``: when the demo night ends."""
    local = now.astimezone(ZoneInfo(cfg.demo_tz))
    wake = local.replace(hour=cfg.demo_wake_hour, minute=0, second=0, microsecond=0)
    return (wake if wake <= local else wake - timedelta(days=1)).astimezone(UTC)


def _same(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def _basic_password(header: str) -> str | None:
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        return base64.b64decode(value, validate=True).decode().partition(":")[2]
    except (binascii.Error, UnicodeDecodeError):
        return None


class AccessGate:
    """Password for people, bearer token for MCP clients; health check and signed webhooks open.

    Pure ASGI (not BaseHTTPMiddleware) so MCP's streamed responses pass straight through.
    A no-op for whichever secret is unset, which is how local development runs.
    """

    OPEN = {"/healthz", "/webhooks/ring"}

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] in self.OPEN:
            return await self.app(scope, receive, send)
        auth = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        if scope["path"].startswith("/mcp"):
            ok = not cfg.mcp_token or _same(auth, f"Bearer {cfg.mcp_token}")
            challenge = 'Bearer realm="Tidal MCP"'
        else:
            pw = _basic_password(auth)
            ok = cfg.dashboard_open or not cfg.password or (pw is not None and _same(pw, cfg.password))
            challenge = 'Basic realm="Tidal", charset="UTF-8"'
        if ok:
            return await self.app(scope, receive, send)
        resp = JSONResponse({"detail": "authentication required"}, 401, headers={"WWW-Authenticate": challenge})
        await resp(scope, receive, send)


def _restore_demo() -> None:
    """Load the bundled demo night into an empty store (fresh container, ephemeral disk)."""
    if store.is_empty() and (DEMO_DIR / "tidal.db").exists():
        shutil.copy(DEMO_DIR / "tidal.db", cfg.db_path)
        store.init()
        renders = cfg.media_dir / "renders"
        for f in (DEMO_DIR / "renders").glob("*.mp4"):
            shutil.copy(f, renders / f.name)
        log.info("demo mode: restored bundled night from %s", DEMO_DIR)


async def _redate_demo_loop() -> None:
    while True:
        await asyncio.sleep(1800)
        await asyncio.to_thread(store.redate_demo, _demo_morning(datetime.now(UTC)))


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
    cfg.check_public()
    store.init()
    tasks = []
    if cfg.demo:
        _restore_demo()
        log.info("demo mode: re-dated %d readings", store.redate_demo(_demo_morning(datetime.now(UTC))))
        tasks.append(asyncio.create_task(_redate_demo_loop()))
    elif cfg.checkin_minutes > 0:
        tasks.append(asyncio.create_task(_checkin_loop()))
    if not cfg.ring_hmac_key:
        log.warning("RING_HMAC_KEY is not set: every Ring webhook will be rejected")
    log.info("Tidal ready — %s, Ring API %s, watching %s", "DEMO" if cfg.demo else "live", cfg.ring_base_url,
             cfg.watch_devices or "all cameras")
    async with _mcp.session_manager.run():
        yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Tidal", version="0.1.0", lifespan=lifespan)
app.add_middleware(AccessGate)
# Alexa+ MCP on the same host and port as the API (one service to deploy). Host-header
# (DNS-rebinding) checks only apply on localhost; a public deployment relies on the bearer token.
_mcp = mcp_server.mcp
app.router.routes.extend(
    _mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0" if cfg.public else "127.0.0.1").routes
)
_upload_lock = asyncio.Lock()  # one analysis at a time: each holds the decoded clip in memory
(cfg.media_dir / "renders").mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=cfg.media_dir), name="media")
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


@app.post("/webhooks/ring")
async def ring_webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
    if not cfg.ring_hmac_key:
        raise HTTPException(503, "webhook signing key not configured")
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
    if cfg.demo:
        raise HTTPException(403, "not available in the demo (there is no real camera)")
    if mode == "clip":
        background.add_task(_clip_job, device_id, datetime.now(UTC) - timedelta(milliseconds=cfg.clip_ms))
        return {"ok": True, "mode": "clip", "queued": True}
    reading = await _live_checkin(device_id)
    if reading is None:
        raise HTTPException(status_code=502, detail="live check-in failed (is this a real, online camera?)")
    return {"ok": True, "mode": "live", "reading": summary_view(reading)}


@app.post("/api/analyze")
async def analyze_upload(file: UploadFile = File(...)) -> dict:
    if not cfg.uploads:
        raise HTTPException(403, "uploads are turned off on this deployment")
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "video too large (200 MB max)")
    async with _upload_lock:
        try:
            reading = await asyncio.to_thread(
                pipeline.process_clip_bytes, data, device_id="upload", device_name=file.filename or "Uploaded video",
                source="upload", max_seconds=cfg.upload_max_s,
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
    return {"readings": store.since(hours), "window_end": store.window_end().isoformat()}


@app.get("/api/reading/{reading_id}")
def reading(reading_id: int) -> dict:
    r = store.get(reading_id)
    if not r:
        raise HTTPException(404, "no such reading")
    return {"reading": r}


@app.get("/api/night")
def night(hours: float = 12.0, tz: str | None = None) -> dict:
    return summary.night_summary(hours, tz)


@app.get("/api/config")
def deployment_config() -> dict:
    return {"demo": cfg.demo, "uploads": cfg.uploads, "upload_max_s": cfg.upload_max_s, "person": cfg.person_name}


@app.get("/api/cameras")
def list_cameras() -> dict:
    if cfg.demo:
        return {"cameras": [], "demo": True}
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
