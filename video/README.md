# Making the demo video

`make_video.py` builds the whole ~2:40 video from the shot list (docs/SUBMISSION.md §3):

```bash
.venv/bin/pip install pillow        # once
.venv/bin/python video/make_video.py
open video/out/tidal_demo.mp4       # + tidal_demo.srt for YouTube captions
```

It always builds a complete cut. Shots 1, 2, 7, 8 and 9 are generated (cut from the
magnified clip, or drawn as slides). Shots 3–6 are your screen recordings; until a file
exists, its slot shows a card saying what to record. Until you record your voice, macOS
reads each line as a stand-in so you can judge the pacing.

## 1 · Record the screen (4 clips)

Use **⌘⇧5 → Record Selected Portion** (or QuickTime → New Screen Recording). Record a
16:9 area if you can; anything else is letterboxed. Save into `video/recordings/` with
these exact names (`.mov` or `.mp4`). Each is trimmed to its slot; a short one holds its
last frame. If the good part starts late, set `rec_start` for that shot in
`make_video.py`.

| File | Slot | What to record |
|---|---|---|
| `03_dashboard_top.mov` | 18 s | https://tidal-su02.onrender.com, top of the page: breathing orb, the *Tonight* sentence, the four stats. Hold still. |
| `04_dashboard_night.mov` | 15 s | Scroll slowly down to *Overnight* and *Check-ins*; point at the amber dot, then the amber *Worth a look* row. |
| `05_ring_flow.mov` | 30 s | Terminal, large font: `.venv/bin/python scripts/demo_flow.py`, until the Alexa+ answers print. |
| `06_alexa_mcp.mov` | 20 s | Terminal: `.venv/bin/python scripts/mcp_smoke.py https://tidal-su02.onrender.com/mcp`. |

Before recording:
- Open the hosted site a minute early — the free server sleeps when idle.
- For shot 6, run `export TIDAL_MCP_TOKEN=…` (from Render → tidal → Environment) and
  `clear` **before** you start recording, so the token never appears on screen.
- Terminal: ⌘+ a few times so the text is readable at 1080p; a dark theme matches the slides.
- Browser: hide bookmarks and other tabs; zoom so the dashboard fills the frame.

## 2 · Record your voice (9 lines)

One file per shot: `video/recordings/vo_01.m4a` … `vo_09.m4a` (QuickTime → New Audio
Recording, or Voice Memos). The lines are in docs/SUBMISSION.md §3 and in `SHOTS` in
`make_video.py`. Each must fit its slot — the build warns if one is too long. Leave
about half a second of silence at the start. A quiet room and a headset mic beat a
laptop mic.

If you change a line's wording, change it in `SHOTS` too so the captions match.

## 3 · Build, check, upload

```bash
.venv/bin/python video/make_video.py              # everything
.venv/bin/python video/make_video.py --only 3 6   # just re-do shots 3 and 6
```

Watch `video/out/tidal_demo.mp4` all the way through, then upload to YouTube (public or
unlisted, English) and add `tidal_demo.srt` as the captions file. No music unless it's
royalty-free and yours to use.
