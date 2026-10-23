#!/usr/bin/env python3
"""Analyse any video file from the command line.

    python scripts/analyze.py my_clip.mp4                # JSON report
    python scripts/analyze.py my_clip.mp4 --render out.mp4 --alpha 30
    python scripts/analyze.py data/sleeper_day.mp4 --truth data/sleeper_day.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from tidal_vitals import analyze_frames, render_overlay_video  # noqa: E402
from tidal_vitals.video import read_video  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--render", help="write the side-by-side magnified demo video here")
    ap.add_argument("--alpha", type=float, default=30.0, help="motion magnification factor for --render")
    ap.add_argument("--max-seconds", type=float, default=180.0)
    ap.add_argument("--start", type=float, default=0.0, help="skip this many seconds first")
    ap.add_argument("--truth", help="ground-truth JSON from scripts/make_synthetic.py to score against")
    ap.add_argument("--no-pulse", action="store_true")
    args = ap.parse_args()

    frames, fs = read_video(args.video, max_seconds=args.max_seconds, start_seconds=args.start)
    rep = analyze_frames(frames, fs, pulse=not args.no_pulse)
    b, p = rep.breathing, rep.pulse
    out = {
        "file": args.video, "fps": fs, "seconds": round(len(frames) / fs, 1),
        "status": rep.status, "reason": rep.status_reason,
        "breathing": {"rate_bpm": b.rate_bpm, "quality": b.quality, "snr_db": b.snr_db,
                      "low_motion_intervals": b.low_motion_intervals,
                      "gross_motion_fraction": b.gross_motion_fraction, "roi_blocks": len(b.roi_blocks),
                      "windows": b.windows},
        "pulse": None if p is None else {"rate_bpm": p.rate_bpm, "quality": p.quality, "snr_db": p.snr_db,
                                         "skin_fraction": p.skin_fraction, "experimental": True},
    }
    if args.truth:
        gt = json.loads(Path(args.truth).read_text())
        true_rr = float(np.mean(gt["resp_rate_bpm"]))
        out["score"] = {
            "resp_true": round(true_rr, 2),
            "resp_abs_error": None if b.rate_bpm is None else round(abs(b.rate_bpm - true_rr), 2),
            "pulse_true": gt["pulse_rate_bpm"],
            "pulse_abs_error": None if not p or p.rate_bpm is None else round(abs(p.rate_bpm - gt["pulse_rate_bpm"]), 2),
            "pause_true": gt.get("pause"), "pause_found": b.low_motion_intervals,
        }
    print(json.dumps(out, indent=2))
    if args.render:
        path = render_overlay_video(frames, fs, b, args.render, alpha=args.alpha)
        print(f"rendered → {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
