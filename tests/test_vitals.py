"""Physics/maths tests: every estimator is checked against known ground truth."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from tidal_vitals import estimate_breathing, estimate_pulse, magnify
from tidal_vitals import signal as sig
from tidal_vitals.blocks import gross_motion_mask, to_gray
from tidal_vitals.synth import SynthSpec, render
from tidal_vitals.video import read_video, write_video


@pytest.mark.parametrize("bpm", [8.0, 15.0, 30.0])
def test_spectral_peak_recovers_sine(bpm):
    fs = 15.0
    t = np.arange(int(40 * fs)) / fs
    x = np.sin(2 * np.pi * bpm / 60 * t) + 0.3 * np.random.default_rng(0).normal(size=t.size)
    pk = sig.spectral_peak(sig.bandpass(x, fs, sig.RESP_BAND), fs, sig.RESP_BAND)
    assert abs(pk.rate_per_min - bpm) < 0.3
    assert pk.quality > 0.8


def test_detrend_removes_drift_keeps_breathing():
    fs = 15.0
    t = np.arange(int(60 * fs)) / fs
    breath = np.sin(2 * np.pi * 0.25 * t)
    x = breath + 5 * (t / t[-1]) ** 2  # strong slow drift
    y = sig.detrend_smoothness_priors(x, lam=300)
    assert np.corrcoef(y[50:-50], breath[50:-50])[0, 1] > 0.95


def test_low_amplitude_intervals():
    fs = 10.0
    env = np.ones(600)
    env[200:320] = 0.05  # 12 s quiet
    assert sig.low_amplitude_intervals(env, fs, 0.3, 8.0) == [(20.0, 32.0)]


@pytest.fixture(scope="module")
def sleeper():
    spec = SynthSpec(duration_s=70, pause=(40, 52), noise_sigma=2.5, pulse_amplitude=0.01)
    return spec, *render(spec)


def test_breathing_rate_and_pause(sleeper):
    spec, frames, gt = sleeper
    b = estimate_breathing(frames, spec.fps)
    assert b.rate_bpm is not None and abs(b.rate_bpm - np.mean(gt.resp_rate_bpm)) < 0.6
    assert b.quality >= 0.6
    assert any(a <= 46 <= e for a, e in b.low_motion_intervals), b.low_motion_intervals
    # ROI must sit on the torso, not the wall.
    gh, gw = b.grid
    rows = np.array(b.roi_blocks) // gw
    assert np.median(rows) * b.block_px > spec.height * 0.3


def test_pulse_pos(sleeper):
    spec, frames, gt = sleeper
    p = estimate_pulse(frames, spec.fps)
    assert p.rate_bpm is not None and abs(p.rate_bpm - spec.pulse_bpm) < 2.0


def test_empty_room_reports_nothing():
    spec = SynthSpec(duration_s=50, resp_amplitude_px=0.0, pulse_amplitude=0.0, pause=None, noise_sigma=2.5, seed=13)
    frames, _ = render(spec)
    assert estimate_breathing(frames, spec.fps).rate_bpm is None


def test_survives_h264(tmp_path, sleeper):
    spec, frames, gt = sleeper
    path = write_video(frames, spec.fps, tmp_path / "c.mp4", crf=18)
    f2, fs = read_video(path)
    b = estimate_breathing(f2, fs)
    assert b.rate_bpm is not None and abs(b.rate_bpm - np.mean(gt.resp_rate_bpm)) < 0.8


def test_gross_motion_detected_but_breathing_is_not():
    spec = SynthSpec(duration_s=40, pause=None, rollover=(20, 23), noise_sigma=2.5)
    frames, _ = render(spec)
    m = gross_motion_mask(to_gray(frames), spec.fps)
    t = np.arange(len(m)) / spec.fps
    assert m[(t > 20.5) & (t < 22.5)].all()
    assert not m[t < 15].any()


def test_evm_amplifies_subpixel_motion():
    spec = SynthSpec(duration_s=24, pause=None, noise_sigma=1.0)
    frames, _ = render(spec)
    out = magnify(frames, spec.fps, alpha=30, lambda_c=40)
    roi = (slice(110, 160), slice(120, 200))

    def shifts(v):
        g0 = cv2.cvtColor(v[60], cv2.COLOR_RGB2GRAY).astype(np.float32)[roi]
        return np.array([cv2.phaseCorrelate(cv2.cvtColor(v[i], cv2.COLOR_RGB2GRAY).astype(np.float32)[roi], g0)[0][1]
                         for i in range(60, len(v))])

    gain = np.ptp(shifts(out)) / np.ptp(shifts(frames))
    assert gain > 5, gain
