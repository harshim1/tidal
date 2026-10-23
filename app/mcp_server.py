"""Tidal MCP server for Alexa+ (Model Context Protocol, Streamable HTTP transport).

Alexa+ integrations are MCP servers. This one exposes Tidal's readings as
tools so a family member can ask, in plain speech:

    "Alexa, how did Dad sleep?"            → get_night_summary
    "Alexa, how's Dad breathing right now?" → check_in_now / get_latest_vitals
    "Alexa, anything I should know?"        → list_attention_events

Every tool returns structured data plus a ``speech`` field written for voice.

Run:  python -m app.mcp_server          (serves http://0.0.0.0:8001/mcp)
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from . import store, summary
from .config import cfg

INSTRUCTIONS = f"""Tidal measures {cfg.person_name}'s breathing contactlessly from their Ring camera.
Use get_night_summary for "how did they sleep" questions, get_latest_vitals for "how are they now",
check_in_now to take a fresh reading, list_attention_events for "anything I should know".
Read the `speech` field aloud. Tidal is a wellness awareness tool, not a medical device: never diagnose."""

mcp = MCPServer(name="tidal", title="Tidal — contactless breathing for caregivers", instructions=INSTRUCTIONS,
                version="0.1.0")


def _view(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if not r:
        return None
    keep = ("id", "device_name", "started_at", "duration_s", "resp_bpm", "resp_quality", "pulse_bpm",
            "pulse_quality", "status", "status_reason", "pauses", "motion", "video")
    return {k: r.get(k) for k in keep}


@mcp.tool()
def get_latest_vitals(timezone: str | None = None) -> dict[str, Any]:
    """Most recent breathing reading (rate per minute, quality 0-1, status) and a spoken summary.

    timezone: IANA zone of the listener (e.g. "America/New_York") for spoken times.
    """
    r = store.latest(full=False)
    return {"reading": _view(r), "speech": summary.latest_speech(r, timezone)}


@mcp.tool()
def get_night_summary(hours: float = 12.0, timezone: str | None = None) -> dict[str, Any]:
    """Overnight summary: check-ins, median/min/max breathing rate, attention moments, restlessness."""
    return summary.night_summary(hours, timezone)


@mcp.tool()
def list_attention_events(hours: float = 24.0, timezone: str | None = None) -> dict[str, Any]:
    """Moments that may deserve a look: breathing motion paused, rate outside the usual resting range."""
    agg = summary.aggregate(hours, timezone)
    ev = agg["attention_events"]
    if ev:
        speech = f"{len(ev)} moment{'s' if len(ev) != 1 else ''} to know about: " + "; ".join(
            f"at {e['at']}, {e['reason'][0].lower() + e['reason'][1:]}" for e in ev[:3]
        )
    else:
        speech = f"Nothing unusual for {cfg.person_name} in the last {hours:.0f} hours."
    return {"events": ev, "speech": speech}


@mcp.tool()
def get_breathing_trend(hours: float = 168.0) -> dict[str, Any]:
    """Breathing-rate history (one point per check-in) for spotting slow changes over days."""
    rows = store.since(hours)
    pts = [{"t": r["started_at"], "bpm": r["resp_bpm"], "status": r["status"]} for r in rows if r["resp_bpm"]]
    speech = (
        f"I have {len(pts)} readings over the last {hours / 24:.0f} days."
        if pts else "There isn't enough history yet to show a trend."
    )
    return {"points": pts, "speech": speech}


@mcp.tool()
async def check_in_now(device_id: str | None = None, timezone: str | None = None) -> dict[str, Any]:
    """Take a fresh reading now from the Ring camera (≈1 minute of live video), then report it."""
    dev = device_id or (cfg.watch_devices[0] if cfg.watch_devices else None)
    if not dev:
        return {"ok": False, "speech": "No camera is configured for check-ins yet."}
    async with httpx.AsyncClient(timeout=cfg.checkin_seconds + 60) as http:
        resp = await http.post(f"{cfg.api_url}/api/checkin/{dev}")
    if resp.status_code != 200:
        return {"ok": False, "speech": "I couldn't reach the camera just now. I'll keep trying on schedule."}
    r = store.get(resp.json()["reading"]["id"])
    return {"ok": True, "reading": _view(r), "speech": summary.latest_speech(r, timezone)}


def main() -> None:
    store.init()
    mcp.run(
        "streamable-http",
        host=os.environ.get("TIDAL_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("TIDAL_MCP_PORT", "8001")),
        streamable_http_path="/mcp",
    )


if __name__ == "__main__":
    main()
