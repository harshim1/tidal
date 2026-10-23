#!/usr/bin/env python3
"""Build the night of check-ins bundled for TIDAL_DEMO=1 → demo/tidal.db + demo/renders/.

Runs scripts/seed_night.py (real analyses of the synthetic clips) into demo/ so a
hosted demo starts instantly and never decodes video at boot. The server re-dates
these rows on startup, so the snapshot never goes stale.

    python scripts/build_demo_snapshot.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"


def main() -> None:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    os.environ["TIDAL_DB"] = str(DEMO / "tidal.db")
    os.environ["TIDAL_MEDIA_DIR"] = str(DEMO)  # renders land in demo/renders/, matching "renders/<name>"
    sys.path.insert(0, str(ROOT))
    from scripts import seed_night  # noqa: PLC0415 — config must read the env above

    seed_night.main()
    print(f"demo snapshot: {DEMO / 'tidal.db'} + {len(list((DEMO / 'renders').glob('*.mp4')))} renders")


if __name__ == "__main__":
    main()
