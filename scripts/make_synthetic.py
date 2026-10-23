#!/usr/bin/env python3
"""Generate the synthetic ground-truth validation set into data/.

Each clip comes with a JSON file holding the true respiration rate per frame,
pulse rate, breath-hold interval and roll-over interval.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tidal_vitals.synth import SynthSpec, write_dataset  # noqa: E402

# Realistic camera conditions: sensor noise σ≈2.5 grey levels (indoor/low light) and
# H.264 at CRF 18 (≈ a 1080p camera stream at a few Mbit/s, scaled to 320 px).
CAM = dict(noise_sigma=2.5)
DATASET: dict[str, tuple[SynthSpec, int]] = {
    # name: (spec, crf)
    "sleeper_day": (SynthSpec(**CAM), 18),  # 14→16 /min, breath-hold 52–64 s, pulse 64
    "sleeper_night_ir": (SynthSpec(**CAM, night_ir=True, seed=11, resp_bpm_start=12, resp_bpm_end=12.5,
                                   pause=None, rollover=(70, 73)), 18),
    "sleeper_slow": (SynthSpec(**CAM, seed=3, resp_bpm_start=8.5, resp_bpm_end=9.0, pause=None, duration_s=75), 18),
    "sleeper_fast": (SynthSpec(**CAM, seed=5, resp_bpm_start=24, resp_bpm_end=26, pause=None, duration_s=60,
                               pulse_bpm=92, pulse_amplitude=0.01), 18),
    "sleeper_faint": (SynthSpec(seed=9, noise_sigma=3.0, resp_amplitude_px=0.15, pause=None, duration_s=75), 18),
    "empty_room": (SynthSpec(**CAM, seed=13, resp_amplitude_px=0.0, pulse_amplitude=0.0, pause=None,
                             duration_s=60), 18),
    # Stress test: near-noiseless scene + heavy compression → encoder skips most blocks.
    # The correct behaviour is to REFUSE to report a rate, not to guess.
    "codec_starved": (SynthSpec(seed=21, noise_sigma=0.8, pause=None, duration_s=60), 28),
}


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "data"
    for name, (spec, crf) in DATASET.items():
        p = write_dataset(out, spec, name, crf=crf)
        print("wrote", p)


if __name__ == "__main__":
    main()
