"""Integration tests: Ring webhook security, pipeline, summaries, MCP tools."""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from ring_sandbox import webhooks


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TIDAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("TIDAL_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("RING_HMAC_KEY", "k3y")
    monkeypatch.setenv("TIDAL_RENDER", "0")
    import app.config

    importlib.reload(app.config)
    mods = {}
    for name in ("store", "summary", "pipeline", "ring", "server", "mcp_server"):
        mods[name] = importlib.reload(importlib.import_module(f"app.{name}"))
    mods["store"].init()
    return mods


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
