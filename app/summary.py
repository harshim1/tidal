"""Night aggregation + natural-language summary.

The numbers are always computed deterministically here. Amazon Bedrock (when
enabled) only *phrases* them — it never invents measurements — and the
template fallback guarantees an answer when Bedrock is off or unreachable.
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import store
from .config import cfg

log = logging.getLogger("tidal.summary")

DISCLAIMER = "Tidal is a wellness awareness tool, not a medical device."


def _local(iso: str, tz: str | None) -> str:
    dt = datetime.fromisoformat(iso)
    try:
        dt = dt.astimezone(ZoneInfo(tz)) if tz else dt.astimezone()
    except Exception:  # noqa: BLE001 - unknown tz name → server local time
        dt = dt.astimezone()
    return dt.strftime("%-I:%M %p")


def aggregate(hours: float = 12.0, tz: str | None = None) -> dict[str, Any]:
    rows = store.since(hours)
    rates = [r["resp_bpm"] for r in rows if r["resp_bpm"] is not None]
    pulses = [r["pulse_bpm"] for r in rows if r.get("pulse_bpm") and (r.get("pulse_quality") or 0) >= 0.6]
    attention = [r for r in rows if r["status"] == "attention"]
    restless = [r for r in rows if r["status"] == "restless"]
    out: dict[str, Any] = {
        "person": cfg.person_name,
        "hours": hours,
        "check_ins": len(rows),
        "measured": len(rates),
        "resp_median": round(statistics.median(rates), 1) if rates else None,
        "resp_min": round(min(rates), 1) if rates else None,
        "resp_max": round(max(rates), 1) if rates else None,
        "pulse_median": round(statistics.median(pulses)) if pulses else None,
        "attention_events": [
            {"at": _local(r["started_at"], tz), "reason": r["status_reason"], "reading_id": r["id"]}
            for r in attention
        ],
        "restless_periods": [_local(r["started_at"], tz) for r in restless],
        "last_check": _local(rows[-1]["started_at"], tz) if rows else None,
        "last_status": rows[-1]["status"] if rows else None,
    }
    return out


def template_speech(agg: dict[str, Any]) -> str:
    who = agg["person"]
    if not agg["check_ins"]:
        return f"I don't have any check-ins for {who} in the last {agg['hours']:.0f} hours yet."
    if not agg["measured"]:
        return (
            f"I checked on {who} {agg['check_ins']} times but couldn't get a clear breathing reading — "
            "they may have been out of view of the camera."
        )
    n = agg["check_ins"]
    times = "once" if n == 1 else "twice" if n == 2 else f"{n} times"
    lo, hi = round(agg["resp_min"]), round(agg["resp_max"])
    rng = f", between {lo} and {hi}" if hi - lo >= 2 else ""
    verb = "was" if agg["measured"] == 1 else "averaged"
    parts = [f"I checked on {who} {times}. Breathing {verb} about {agg['resp_median']:.0f} breaths a minute{rng}."]
    ev = agg["attention_events"]
    if ev:
        first = ev[0]
        more = f", and {len(ev) - 1} other time{'s' if len(ev) > 2 else ''}" if len(ev) > 1 else ""
        reason = first["reason"].rstrip(".")
        parts.append(f"One thing to know: at {first['at']}, {reason[0].lower() + reason[1:]}{more}.")
    else:
        parts.append("Nothing unusual stood out.")
    if agg["restless_periods"]:
        parts.append(f"They were moving around at {', '.join(agg['restless_periods'][:3])}.")
    parts.append(f"The last check was at {agg['last_check']}.")
    return " ".join(parts)


SYSTEM_PROMPT = (
    "You are Tidal, a calm assistant that tells a family member how their loved one slept, using ONLY "
    "the JSON measurements provided. Speak in 2-4 short sentences suitable for a voice assistant. "
    "Never diagnose, never use medical terms like apnea or arrhythmia, never invent numbers. If there "
    "were attention events, mention the time plainly and suggest checking in if it seems unusual for them."
)


def bedrock_speech(agg: dict[str, Any]) -> str | None:
    if not cfg.use_bedrock:
        return None
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=cfg.aws_region)
        resp = client.converse(
            modelId=cfg.bedrock_model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": json.dumps(agg)}]}],
            inferenceConfig={"maxTokens": 220, "temperature": 0.2},
        )
        return resp["output"]["message"]["content"][0]["text"].strip()
    except Exception as exc:  # noqa: BLE001 — any AWS failure falls back to the template
        log.warning("Bedrock unavailable, using template: %s", exc)
        return None


def night_summary(hours: float = 12.0, tz: str | None = None) -> dict[str, Any]:
    agg = aggregate(hours, tz)
    speech = bedrock_speech(agg)
    return {**agg, "speech": speech or template_speech(agg), "phrased_by": "bedrock" if speech else "template",
            "disclaimer": DISCLAIMER}


def latest_speech(r: dict[str, Any] | None, tz: str | None = None) -> str:
    if not r:
        return f"I haven't checked on {cfg.person_name} yet."
    when = _local(r["started_at"], tz)
    if r["resp_bpm"] is None:
        return f"At {when}, I couldn't get a clear breathing reading. {r['status_reason']}"
    s = f"At {when}, {cfg.person_name} was breathing about {r['resp_bpm']:.0f} times a minute. {r['status_reason']}"
    if r.get("pulse_bpm") and (r.get("pulse_quality") or 0) >= 0.6:
        s += f" Estimated pulse was around {r['pulse_bpm']:.0f}."
    return s
