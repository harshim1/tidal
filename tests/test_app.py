"""Integration tests: Ring webhook security, pipeline, summaries, MCP tools."""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from ring_sandbox import webhooks


def _load(tmp_path, monkeypatch, **env):
    base = {"TIDAL_DB": str(tmp_path / "t.db"), "TIDAL_MEDIA_DIR": str(tmp_path / "media"), "RING_HMAC_KEY": "k3y",
            "TIDAL_RENDER": "0", "TIDAL_PUBLIC": "0", "TIDAL_DEMO": "0", "TIDAL_PASSWORD": "", "TIDAL_MCP_TOKEN": ""}
    for k, v in {**base, **env}.items():
        monkeypatch.setenv(k, v)
    import app.config

    importlib.reload(app.config)
    mods = {}
    for name in ("store", "summary", "pipeline", "ring", "mcp_server", "server"):
        mods[name] = importlib.reload(importlib.import_module(f"app.{name}"))
    mods["store"].init()
    return mods


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    return _load(tmp_path, monkeypatch)


def _signed(payload: dict, key: str = "k3y") -> tuple[bytes, dict]:
    body = webhooks.encode(payload)
    return body, {"Content-Type": "application/json", webhooks.SIGNATURE_HEADER: webhooks.sign(key, body)}


def test_webhook_rejects_bad_signature(app_env):
    from fastapi.testclient import TestClient

    with TestClient(app_env["server"].app) as c:
        body, headers = _signed(webhooks.build_event(event_type="motion_detected", device_id="cam1"), key="wrong")
        assert c.post("/webhooks/ring", content=body, headers=headers).status_code == 401


def test_webhook_accepts_and_dedupes(app_env, monkeypatch):
    from fastapi.testclient import TestClient

    jobs = []
    monkeypatch.setattr(app_env["server"], "_clip_job", lambda d, t: jobs.append((d, t)))
    with TestClient(app_env["server"].app) as c:
        payload = webhooks.build_event(event_type="motion_detected", device_id="cam1", sub_type="human")
        body, headers = _signed(payload)
        r1 = c.post("/webhooks/ring", content=body, headers=headers).json()
        r2 = c.post("/webhooks/ring", content=body, headers=headers).json()
    assert r1 == {"ok": True, "accepted": True}
    assert r2["duplicate"] is True
    assert len(jobs) == 1 and jobs[0][0] == "cam1"


def _seed(app_env):
    from tidal_vitals.synth import SynthSpec, render

    spec = SynthSpec(duration_s=60, pause=(35, 47), noise_sigma=2.5)
    frames, _ = render(spec)
    p = app_env["pipeline"]
    r = p.process_frames(frames, spec.fps, device_id="cam1", device_name="Bedroom camera", source="test",
                         started_at=datetime.now(UTC) - timedelta(hours=2))
    return r


def test_pipeline_summary_and_mcp_tools(app_env):
    r = _seed(app_env)
    assert r["status"] == "attention" and abs(r["resp_bpm"] - 15) < 1

    night = app_env["summary"].night_summary(12)
    assert night["check_ins"] == 1 and night["phrased_by"] == "template"
    assert "breaths a minute" in night["speech"] and "quiet" in night["speech"] and "once" in night["speech"]

    m = app_env["mcp_server"]
    tools = asyncio.run(m.mcp.list_tools())
    names = {t.name for t in tools}
    assert {"get_latest_vitals", "get_night_summary", "list_attention_events", "check_in_now",
            "get_breathing_trend"} <= names
    latest = m.get_latest_vitals("America/New_York")
    assert latest["reading"]["id"] == r["id"] and "breathing about" in latest["speech"]
    ev = m.list_attention_events(24)
    assert len(ev["events"]) == 1


def test_api_endpoints(app_env):
    from fastapi.testclient import TestClient

    _seed(app_env)
    with TestClient(app_env["server"].app) as c:
        latest = c.get("/api/latest").json()
        assert latest["reading"]["waveform"]
        assert c.get("/api/night").json()["check_ins"] == 1
        assert len(c.get("/api/readings?hours=12").json()["readings"]) == 1
        assert c.get("/").status_code == 200
        json.dumps(latest)  # serialisable


def test_webhook_fails_closed_without_signing_key(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    mods = _load(tmp_path, monkeypatch, RING_HMAC_KEY="")
    with TestClient(mods["server"].app) as c:
        body, headers = _signed(webhooks.build_event(event_type="motion_detected", device_id="cam1"), key="")
        assert c.post("/webhooks/ring", content=body, headers=headers).status_code == 503


def test_public_mode_refuses_to_start_unprotected(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    mods = _load(tmp_path, monkeypatch, TIDAL_PUBLIC="1", TIDAL_PASSWORD="pw")
    with pytest.raises(RuntimeError, match="TIDAL_MCP_TOKEN"):
        with TestClient(mods["server"].app):
            pass


def test_password_and_mcp_token(tmp_path, monkeypatch):
    import base64

    from fastapi.testclient import TestClient

    mods = _load(tmp_path, monkeypatch, TIDAL_PUBLIC="1", TIDAL_PASSWORD="s3cret", TIDAL_MCP_TOKEN="t0k")
    basic = lambda pw: {"Authorization": "Basic " + base64.b64encode(f"anyone:{pw}".encode()).decode()}  # noqa: E731
    with TestClient(mods["server"].app) as c:
        assert c.get("/healthz").status_code == 200
        r = c.get("/api/latest")
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
        assert c.get("/", headers=basic("wrong")).status_code == 401
        assert c.get("/api/latest", headers=basic("s3cret")).status_code == 200
        assert c.get("/media/renders/x.mp4").status_code == 401  # renders are bedroom video
        assert c.post("/mcp", json={}).status_code == 401
        assert c.post("/mcp", json={}, headers=basic("s3cret")).status_code == 401  # people's password isn't the token
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
        r = c.post("/mcp", json=init, headers={"Authorization": "Bearer t0k",
                                                "Accept": "application/json, text/event-stream"})
        assert r.status_code == 200 and "tidal" in r.text


def test_demo_mode_restores_and_redates_bundled_night(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    mods = _load(tmp_path, monkeypatch, TIDAL_DEMO="1", TIDAL_UPLOADS="0")
    if not (mods["server"].DEMO_DIR / "tidal.db").exists():
        pytest.skip("demo snapshot not built (python scripts/build_demo_snapshot.py)")
    with TestClient(mods["server"].app) as c:
        latest = c.get("/api/latest").json()["reading"]
        age = datetime.now(UTC) - datetime.fromisoformat(latest["started_at"])
        assert timedelta(0) < age < timedelta(minutes=30)
        assert c.get("/api/night").json()["check_ins"] > 10
        assert c.get(f"/media/{latest['video']}").status_code == 200
        assert c.get("/api/config").json() == {"demo": True, "uploads": False, "upload_max_s": 180.0,
                                               "person": "Dad"}
        assert c.post("/api/checkin/cam1").status_code == 403
        assert c.post("/api/analyze", files={"file": ("a.mp4", b"x")}).status_code == 403
    speech = asyncio.run(mods["mcp_server"].check_in_now())["speech"]
    assert speech.startswith("This is the Tidal demo")
