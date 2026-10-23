#!/usr/bin/env python3
"""Assemble the Tidal demo video (≈2:40) from the shot list in docs/SUBMISSION.md §3.

    python video/make_video.py            # → video/out/tidal_demo.mp4 + tidal_demo.srt

What it uses, per shot:
* shots cut from media/demo_side_by_side.mp4 and the slides (physics, validation,
  title) are generated here — nothing to record;
* screen recordings you drop in video/recordings/ (names below); a missing one becomes a
  placeholder card saying what to record, so a full rough cut always builds;
* your voice: video/recordings/vo_01.m4a … vo_09.m4a (any of m4a/wav/mp3/aiff/mov). A
  missing one is read by macOS `say` as a stand-in so you can judge the pacing.

Captions are burned in (ffmpeg here has no drawtext/subtitles, so they are drawn with
Pillow and overlaid) and also written as an .srt for YouTube. `--no-captions` skips the
burn-in. Needs ffmpeg and Pillow (`pip install pillow`).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REC, BUILD, OUT = HERE / "recordings", HERE / "build", HERE / "out"
SOURCE = ROOT / "media" / "demo_side_by_side.mp4"
W, H, FPS = 1920, 1080, 30
SAFE_BOTTOM = 840  # slides keep text above this; captions use the band below

# Dashboard palette (app/static/index.html) so slides and the product look like one thing.
BG, PANEL, LINE = (11, 17, 26), (17, 26, 38), (34, 48, 68)
INK, DIM, FAINT = (232, 237, 243), (139, 152, 169), (91, 104, 121)
TIDE, WARN = (94, 203, 214), (245, 176, 92)

SERIF = "/System/Library/Fonts/Supplemental/Georgia.ttf"
SANS = "/System/Library/Fonts/HelveticaNeue.ttc"
MONO = "/System/Library/Fonts/Menlo.ttc"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


@dataclass
class Shot:
    n: int
    dur: float
    vo: str
    kind: str  # "clip" | "rec" | "slide"
    clip: dict = field(default_factory=dict)  # {"start": s, "crop": "w:h:x:y"} for kind="clip"
    rec: str = ""  # recording file stem in video/recordings/ for kind="rec"
    rec_start: float = 0.0  # seconds to skip at the start of that recording
    what: str = ""  # what to record (placeholder card + checklist)
    slide: str = ""  # renderer name for kind="slide"


SHOTS = [
    Shot(1, 12, "Imagine this is a Ring camera in your dad's bedroom. He lives alone, it's 2 a.m., and he's "
         "asleep. Is he breathing? From this picture, you can't tell.",
         "clip", clip={"start": 8, "crop": "480:360:0:44"}),
    Shot(2, 15, "Tidal can. Each breath moves his chest a few millimetres, less than a pixel on a home camera. "
         "Tidal measures that motion, and here it magnifies it so you can see it.",
         "clip", clip={"start": 41}),
    Shot(3, 18, "Wearables get forgotten, uncharged, or refused. Tidal needs no new hardware: it works on the "
         "Ring camera already in the room. This is what the family sees in the morning. One calm answer.",
         "rec", rec="03_dashboard_top",
         what="Hosted dashboard, top of the page: breathing orb, the Tonight sentence, the four stats. "
              "Hold still; maybe move the mouse slowly over the Tonight sentence."),
    Shot(4, 15, "Through the night it checks in every half hour. Most check-ins are steady. One moment is worth "
         "a look: breathing motion went quiet for about nine seconds. Amber, never red. Tidal doesn't diagnose.",
         "rec", rec="04_dashboard_night",
         what="Scroll slowly down to Overnight and Check-ins; point at the amber dot, then the amber "
              "'Worth a look' row."),
    Shot(5, 30, "Under the hood: Ring sends Tidal a signed webhook when there's motion. Tidal verifies the "
         "signature, downloads the clip and measures it. But a sleeping person doesn't trigger motion, so at "
         "night Tidal also opens Ring's live video on a schedule and checks in by itself.",
         "rec", rec="05_ring_flow",
         what="Terminal, large font: .venv/bin/python scripts/demo_flow.py — let it run until the "
              "Alexa+ answers print."),
    Shot(6, 20, "And nobody has to open an app. Tidal is an Alexa+ integration. Ask 'How did Dad sleep?', and "
         "Alexa answers with the same sentence the dashboard shows.",
         "rec", rec="06_alexa_mcp",
         what="Terminal: mcp_smoke.py against https://tidal-su02.onrender.com/mcp (export "
              "TIDAL_MCP_TOKEN before recording so it never shows)."),
    Shot(7, 25, "The physics is MIT's Eulerian Video Magnification and remote-sensing research. We validated it "
         "on footage with a known breathing rate, through real video compression: within a fifth of a breath "
         "per minute, even at 0.15-pixel motion and in infrared night vision.",
         "slide", slide="physics"),
    Shot(8, 15, "Just as important: when it can't see clearly, like an empty bed or heavy compression, it says "
         "'no clear reading' instead of guessing. Our first version got fooled by the video encoder; that's "
         "why the confidence check exists.",
         "slide", slide="validation"),
    Shot(9, 10, "Tidal. Peace of mind from the camera you already own. It's a wellness tool, not a medical "
         "device, and it's open source.",
         "slide", slide="title"),
]


# ---------------------------------------------------------------- drawing

def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


def wrap(d: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=f) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def label(d: ImageDraw.ImageDraw, xy: tuple[int, int], text: str) -> None:
    d.text(xy, text.upper(), font=font(SANS, 26), fill=DIM, spacing=4)


def wordmark(d: ImageDraw.ImageDraw, x: int, y: int, scale: float = 1.0) -> None:
    """The two-wave Tidal mark + wordmark."""
    import math

    for k, (amp, alpha) in enumerate(((14, TIDE), (10, (54, 120, 128)))):
        pts = [(x + i, y + 30 * scale + k * 22 * scale + amp * scale * math.sin(i / (16 * scale)))
               for i in range(0, int(150 * scale))]
        d.line(pts, fill=alpha, width=max(3, int(5 * scale)))
    d.text((x + 175 * scale, y - 18 * scale), "tidal", font=font(SERIF, int(96 * scale)), fill=INK)


def slide_physics() -> Image.Image:
    img, d = canvas()
    label(d, (120, 90), "How Tidal sees a breath")
    d.text((120, 140), "Brightness change ≈ motion × image gradient", font=font(SERIF, 64), fill=INK)
    d.text((120, 232), "A chest moving 0.3 px shifts each textured pixel by a few grey levels — "
           "measurable once you average and filter.", font=font(SANS, 32), fill=DIM)
    steps = [("Pixels", "8×8 blocks,\nlight removed"), ("Codec repair", "undo H.264\nskip blocks"),
             ("Band-pass", "0.1–0.7 Hz\n(6–42 /min)"), ("Find the chest", "coherent blocks\n+ PCA"),
             ("Rate", "FFT peak,\nsub-bin accurate"), ("Confidence", "report only\nif ≥ 0.6")]
    bw, gap, y0 = 255, 34, 400
    x = 120
    for i, (title, body) in enumerate(steps):
        last = i == len(steps) - 1
        d.rounded_rectangle((x, y0, x + bw, y0 + 230), 22, fill=PANEL, outline=WARN if last else LINE, width=3)
        d.text((x + 24, y0 + 28), title, font=font(SANS, 34), fill=WARN if last else TIDE)
        d.multiline_text((x + 24, y0 + 92), body, font=font(SANS, 28), fill=INK, spacing=10)
        if not last:
            ax = x + bw + 6
            d.line((ax, y0 + 115, ax + gap - 12, y0 + 115), fill=FAINT, width=4)
            d.polygon([(ax + gap - 12, y0 + 105), (ax + gap - 2, y0 + 115), (ax + gap - 12, y0 + 125)], fill=FAINT)
        x += bw + gap
    d.text((120, 690), "Magnifying the same signal (Eulerian Video Magnification) makes it visible:",
           font=font(SANS, 32), fill=DIM)
    d.text((120, 738), "the right-hand panel you saw is the camera image with the breathing band amplified ×30.",
           font=font(SANS, 32), fill=DIM)
    d.text((120, SAFE_BOTTOM - 40), "Wu et al., SIGGRAPH 2012 · Wang et al., IEEE TBME 2017 · Tarvainen et al., IEEE TBME 2002",
           font=font(MONO, 24), fill=FAINT)
    return img


def slide_validation() -> Image.Image:
    img, d = canvas()
    label(d, (120, 90), "Validated on known breathing, through real H.264")
    d.text((120, 140), "Accurate when it can see. Silent when it can't.", font=font(SERIF, 64), fill=INK)
    rows = [("Typical sleeper + 12 s breath-hold", "15.0", "14.9  · pause found", False),
            ("Infrared night vision + roll-over", "12.25", "12.24", False),
            ("Slow / fast breathing", "8.75 / 25.0", "8.77 / 25.01", False),
            ("Faint: 0.15-pixel motion", "15.0", "14.9", False),
            ("Empty room", "—", "No clear reading", True),
            ("Heavy compression (codec-starved)", "—", "No clear reading", True)]
    cols = (120, 1020, 1330)
    y, row = 270, 74
    for i, txt in enumerate(("Clip", "Truth /min", "Tidal")):
        d.text((cols[i], y), txt.upper(), font=font(SANS, 24), fill=FAINT)
    y += 50
    for clip, truth, tidal, refusal in rows:
        d.line((120, y, W - 120, y), fill=LINE, width=2)
        if refusal:
            d.rectangle((120, y + 2, W - 120, y + row - 2), fill=(40, 34, 26))
        d.text((cols[0], y + 17), clip, font=font(SANS, 36), fill=INK)
        d.text((cols[1], y + 17), truth, font=font(MONO, 34), fill=DIM)
        d.text((cols[2], y + 17), tidal, font=font(MONO if not refusal else SANS, 34), fill=WARN if refusal else TIDE)
        y += row
    d.line((120, y, W - 120, y), fill=LINE, width=2)
    d.text((120, y + 26), "Reproduce: python scripts/analyze.py data/<clip>.mp4 --truth data/<clip>.json",
           font=font(MONO, 26), fill=FAINT)
    return img


def slide_title() -> Image.Image:
    img, d = canvas()
    wordmark(d, 120, 250, 1.6)
    d.text((124, 450), "Peace of mind from the camera you already own.", font=font(SERIF, 60), fill=INK)
    d.text((124, 545), "Ring  ·  Alexa+  ·  AWS  ·  open source (MIT)", font=font(SANS, 38), fill=TIDE)
    d.text((124, 660), "github.com/harshim1/tidal", font=font(MONO, 34), fill=INK)
    d.text((124, 712), "tidal-su02.onrender.com", font=font(MONO, 34), fill=INK)
    d.text((124, SAFE_BOTTOM - 40), "A wellness awareness tool — not a medical device.", font=font(SANS, 30), fill=FAINT)
    return img


def placeholder(shot: Shot) -> Image.Image:
    img, d = canvas()
    label(d, (120, 120), f"Shot {shot.n} · screen recording goes here")
    d.text((120, 180), f"video/recordings/{shot.rec}.mov", font=font(MONO, 48), fill=WARN)
    y = 300
    for line in wrap(d, shot.what, font(SANS, 44), W - 240):
        d.text((120, y), line, font=font(SANS, 44), fill=INK)
        y += 62
    d.text((120, SAFE_BOTTOM - 40), f"{shot.dur:.0f} s slot — this card disappears once the file exists.",
           font=font(SANS, 30), fill=FAINT)
    return img


SLIDES = {"physics": slide_physics, "validation": slide_validation, "title": slide_title}


def caption_png(text: str, path: Path) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(SANS, 44)
    lines = wrap(d, text, f, 1500)
    lh = 58
    box_h = lh * len(lines) + 36
    widest = max(d.textlength(line, font=f) for line in lines)
    top = H - 70 - box_h
    d.rounded_rectangle(((W - widest) / 2 - 28, top, (W + widest) / 2 + 28, top + box_h), 16, fill=(5, 8, 13, 200))
    for i, line in enumerate(lines):
        d.text(((W - d.textlength(line, font=f)) / 2, top + 16 + i * lh), line, font=f, fill=INK)
    img.save(path)


def caption_chunks(text: str, max_chars: int = 80) -> list[str]:
    """Split a voice-over into caption-sized pieces at sentence, then clause, boundaries."""
    out = []
    for sent in re.split(r"(?<=[.?!])\s+", text.strip()):
        while len(sent) > max_chars:
            cut = max((sent.rfind(p, 0, max_chars) for p in (", ", ": ", "; ")), default=-1)
            cut = cut + 1 if cut > 20 else sent.rfind(" ", 0, max_chars)
            out.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if sent:
            out.append(sent)
    return out


# ---------------------------------------------------------------- media

def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def find(stem: str, exts: tuple[str, ...]) -> Path | None:
    for ext in exts:
        p = REC / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def voice(shot: Shot, use_say: bool) -> tuple[Path | None, bool]:
    """(audio file, is_stand_in)."""
    p = find(f"vo_{shot.n:02d}", (".m4a", ".wav", ".mp3", ".aiff", ".mov", ".mp4"))
    if p:
        return p, False
    if not use_say or not shutil.which("say"):
        return None, True
    out = BUILD / f"say_{shot.n:02d}.aiff"
    run(["say", "-v", "Samantha", "-r", "175", "-o", str(out), shot.vo])
    return out, True


FIT = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
       f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0b111a,setsar=1,fps={FPS}")


def build_segment(shot: Shot, burn: bool, use_say: bool) -> tuple[Path, list[tuple[float, float, str]], str]:
    seg = BUILD / f"seg_{shot.n:02d}.mp4"
    notes = []
    # video input
    if shot.kind == "clip":
        vin = ["-ss", str(shot.clip["start"]), "-i", str(SOURCE)]
        pre = f"crop={shot.clip['crop']}," if "crop" in shot.clip else ""
        vchain = f"[0:v]{pre}{FIT},tpad=stop_mode=clone:stop_duration={shot.dur}"
    else:
        rec = find(shot.rec, (".mov", ".mp4", ".m4v")) if shot.kind == "rec" else None
        if rec:
            vin = ["-ss", str(shot.rec_start), "-i", str(rec)]
            vchain = f"[0:v]{FIT},tpad=stop_mode=clone:stop_duration={shot.dur}"
            if probe_duration(rec) - shot.rec_start < shot.dur:
                notes.append(f"recording shorter than {shot.dur:.0f} s; last frame is held")
        else:
            still = BUILD / f"card_{shot.n:02d}.png"
            (SLIDES[shot.slide]() if shot.kind == "slide" else placeholder(shot)).save(still)
            vin = ["-loop", "1", "-framerate", str(FPS), "-i", str(still)]
            vchain = f"[0:v]{FIT}"
            if shot.kind == "rec":
                notes.append(f"placeholder — record video/recordings/{shot.rec}.mov")
    # audio input
    vo, stand_in = voice(shot, use_say)
    ain = ["-i", str(vo)] if vo else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    vo_len = probe_duration(vo) if vo else shot.dur
    if vo and vo_len > shot.dur - 0.3:
        notes.append(f"voice-over {vo_len:.1f} s is longer than the {shot.dur:.0f} s slot — trim or speak faster")
    if stand_in and vo:
        notes.append("computer voice stand-in")
    # captions spread over the spoken part, in proportion to their length
    chunks = caption_chunks(shot.vo)
    spoken = min(vo_len, shot.dur - 0.2)
    total = sum(len(c) for c in chunks)
    cues, t = [], 0.15
    for c in chunks:
        dt = spoken * len(c) / total
        cues.append((t, min(t + dt, shot.dur - 0.05), c))
        t += dt
    inputs, chain, last = vin + ain, [vchain + "[v0]"], "v0"
    if burn:
        for i, (a, b, text) in enumerate(cues):
            png = BUILD / f"cap_{shot.n:02d}_{i}.png"
            caption_png(text, png)
            inputs += ["-i", str(png)]
            chain.append(f"[{last}][{i + 2}:v]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})'[v{i + 1}]")
            last = f"v{i + 1}"
    chain.append(f"[{last}]format=yuv420p[vout]")
    chain.append(f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad[aout]")
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chain),
         "-map", "[vout]", "-map", "[aout]", "-t", f"{shot.dur}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(seg)])
    return seg, cues, "; ".join(notes)


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-captions", action="store_true", help="don't burn captions in (the .srt is still written)")
    ap.add_argument("--no-say", action="store_true", help="silence instead of the computer-voice stand-in")
    ap.add_argument("--only", type=int, nargs="*", help="rebuild just these shot numbers, then re-join all")
    args = ap.parse_args()
    if not SOURCE.exists():
        sys.exit(f"missing {SOURCE}")
    for d in (REC, BUILD, OUT):
        d.mkdir(parents=True, exist_ok=True)

    segs, srt, offset = [], [], 0.0
    for shot in SHOTS:
        seg = BUILD / f"seg_{shot.n:02d}.mp4"
        cues_file = BUILD / f"cues_{shot.n:02d}.json"
        if args.only and shot.n not in args.only and seg.exists() and cues_file.exists():
            cues, note = json.loads(cues_file.read_text()), "kept"
        else:
            seg, cues, note = build_segment(shot, not args.no_captions, not args.no_say)
            cues_file.write_text(json.dumps(cues))
        print(f"shot {shot.n}  {offset:5.1f}–{offset + shot.dur:5.1f}s  {shot.kind:5s}  {note}")
        segs.append(seg)
        srt += [(offset + a, offset + b, text) for a, b, text in cues]
        offset += shot.dur

    listing = BUILD / "segments.txt"
    listing.write_text("".join(f"file '{s}'\n" for s in segs))
    final = OUT / "tidal_demo.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(final)])
    (OUT / "tidal_demo.srt").write_text("".join(
        f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n\n" for i, (a, b, text) in enumerate(srt, 1)))
    print(f"\n{final}  ({probe_duration(final):.1f} s)\n{OUT / 'tidal_demo.srt'}")


if __name__ == "__main__":
    main()
