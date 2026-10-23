#!/usr/bin/env python3
"""Fill the database with a realistic night of check-ins for the demo dashboard.

Every reading is a *real* analysis of a segment of the synthetic validation
clips (nothing is typed in by hand); only the timestamps are back-dated across
the last 12 hours. Rows are tagged source="demo" so they are never confused with
camera data.

    python scripts/make_synthetic.py && python scripts/seed_night.py
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import pipeline, store  # noqa: E402
from app.config import cfg  # noqa: E402
from tidal_vitals.video import read_video  # noqa: E402


def main(n: int = 22, every_min: int = 30) -> None:
    store.init()
    store.clear()
    rng = random.Random(4)
    clips = {name: read_video(ROOT / "data" / f"{name}.mp4", max_seconds=None)
             for name in ("sleeper_night_ir", "sleeper_day", "sleeper_slow")}
    now = datetime.now(UTC)
    attention_slot, restless_slot = n - 8, n - 14
    render_default = cfg.render_video
    for i in range(n):
        when = now - timedelta(minutes=every_min * (n - i)) + timedelta(seconds=rng.randint(-90, 90))
        if i == attention_slot:
            frames, fs = clips["sleeper_day"]
            seg = frames[int(30 * fs): int(85 * fs)]  # contains the breath-hold
        elif i == restless_slot:
            frames, fs = clips["sleeper_night_ir"]
            seg = frames[int(62 * fs): int(80 * fs)]  # roll-over dominates this short window
        else:
            frames, fs = clips["sleeper_night_ir"]
            start = rng.uniform(0, 25)
            seg = frames[int(start * fs): int((start + 45 + rng.uniform(0, 10)) * fs)]
        cfg.render_video = render_default and i in (attention_slot, n - 1)
        r = pipeline.process_frames(seg, fs, device_id="demo-bedroom", device_name="Bedroom camera",
                                    source="demo", started_at=when)
        print(f"{when:%H:%M}  {r['resp_bpm']}  {r['status']}")
    cfg.render_video = render_default


if __name__ == "__main__":
    main()
