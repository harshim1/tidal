#!/usr/bin/env python3
"""End-to-end Ring → Tidal → Alexa+ demo, fully offline.

Starts the ring-sandbox Ring API emulator (serving your clip as the camera's
recording), the Tidal server and the Tidal MCP server; adds a "Bedroom camera",
registers Tidal's webhook, injects a signed motion event, waits for the
reading, then asks the MCP server what Alexa+ would say.

    python scripts/demo_flow.py                         # uses data/sleeper_day.mp4
    python scripts/demo_flow.py --clip my_recording.mp4
    python scripts/demo_flow.py --keep                  # leave servers running for the dashboard
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
EMU, API, MCP = "http://127.0.0.1:8787", "http://127.0.0.1:8000", "http://127.0.0.1:8001/mcp"
TOKEN, HMAC = "sandbox-token", "dev-hmac-key"


def wait_up(url: str, timeout: float = 30) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise SystemExit(f"{url} did not come up")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default=str(ROOT / "data" / "sleeper_day.mp4"))
    ap.add_argument("--keep", action="store_true", help="keep servers running after the demo")
    args = ap.parse_args()

    media = ROOT / "media" / "ring"
    media.mkdir(parents=True, exist_ok=True)
    shutil.copy(args.clip, media / "default.mp4")  # emulator serves this as every camera's recording

    env = {**os.environ, "RING_BASE_URL": EMU, "RING_ACCESS_TOKEN": TOKEN, "RING_HMAC_KEY": HMAC,
           "RING_MEDIA_ORIGINS": "", "TIDAL_API_URL": API, "PYTHONPATH": str(ROOT)}
    procs = [
        subprocess.Popen([sys.executable, "-m", "ring_sandbox.cli", "serve", "--port", "8787", "--media-dir",
                          str(media), "--token", TOKEN], env=env),
        subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--port", "8000"], cwd=ROOT, env=env),
        subprocess.Popen([sys.executable, "-m", "app.mcp_server"], cwd=ROOT, env={**env, "TIDAL_MCP_HOST": "127.0.0.1"}),
    ]
    try:
        wait_up(f"{EMU}/_sandbox/health")
        wait_up(f"{API}/healthz")
        time.sleep(1.5)

        dev = httpx.post(f"{EMU}/_sandbox/devices", json={"kind": "camera", "name": "Bedroom camera"}).json()
        device_id = dev.get("id") or dev.get("data", {}).get("id")
        httpx.post(f"{EMU}/_sandbox/webhooks", json={"url": f"{API}/webhooks/ring", "signing_key": HMAC})
        before = httpx.get(f"{API}/api/latest").json().get("reading")
        before_id = before["id"] if before else 0
        print(f"\n● Bedroom camera = {device_id}\n● injecting signed motion event…")
        httpx.post(f"{EMU}/_sandbox/events", json={"device_id": device_id, "type": "motion_detected",
                                                    "sub_type": "human", "duration_ms": 120_000})
        t0 = time.time()
        reading = None
        while time.time() - t0 < 180:
            r = httpx.get(f"{API}/api/latest").json().get("reading")
            if r and r["id"] != before_id and r["device_id"] == device_id:
                reading = r
                break
            time.sleep(1)
        if not reading:
            raise SystemExit("no reading arrived — check the server log above")
        print(f"\n● reading #{reading['id']} in {time.time() - t0:.1f}s: {reading['resp_bpm']} breaths/min, "
              f"quality {reading['resp_quality']}, status={reading['status']} — {reading['status_reason']}")
        if reading.get("video"):
            print(f"● magnified render: {API}/media/{reading['video']}")

        sys.path.insert(0, str(ROOT / "scripts"))
        from mcp_smoke import main as mcp_main

        print("\n● asking the Alexa+ MCP server…")
        asyncio.run(mcp_main(MCP))
        print(f"\n● dashboard: {API}/")
        if args.keep:
            print("servers running — Ctrl-C to stop")
            signal.pause()
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
