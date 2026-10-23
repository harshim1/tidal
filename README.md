# Tidal

**Contactless breathing monitoring from the Ring camera you already own.**
Video-magnification physics measures chest motion smaller than a pixel, so families can
check on an aging parent overnight — on a calm dashboard or by asking Alexa+.

> Wellness awareness tool, not a medical device.

## Quick start (fully offline, ~3 minutes)

```bash
pip install -r requirements.txt          # needs ffmpeg on PATH for browser-playable renders
python scripts/make_synthetic.py         # ground-truth validation clips → data/
python -m pytest                         # 15 tests
python scripts/demo_flow.py --keep       # Ring emulator → signed webhook → reading → Alexa+ MCP
# open http://127.0.0.1:8000
```

`demo_flow.py` starts the ring-sandbox Ring API emulator serving a clip as the camera's
recording, the Tidal API and the Tidal MCP server; it adds a "Bedroom camera",
registers the webhook, injects a signed motion event and prints what Alexa+ would say.
Use your own footage with `--clip my_video.mp4`.

Fill the dashboard with a night of real analyses: `python scripts/seed_night.py`.

## Analyse any video

```bash
python scripts/analyze.py my_clip.mp4 --render my_clip_magnified.mp4
python scripts/analyze.py data/sleeper_day.mp4 --truth data/sleeper_day.json
```

## Run against a real Ring account

```bash
cp .env.example .env    # set RING_ACCESS_TOKEN, RING_HMAC_KEY, TIDAL_DEVICES
uvicorn app.server:app --port 8000      # webhook: POST /webhooks/ring (expose via HTTPS)
python -m app.mcp_server                # Alexa+ MCP: http://<host>:8001/mcp
```

Night check-ins over WHEP live video: `TIDAL_CHECKIN_MINUTES=15`.
Bedrock phrasing: `TIDAL_BEDROCK=1` with AWS credentials and model access.

## Repository map

| Path | What |
|---|---|
| `tidal_vitals/` | open-source signal library (EVM, breathing, POS pulse, confidence) |
| `app/` | Ring integration, API server, MCP server, Bedrock summary, dashboard |
| `scripts/` | demo flow, analysis CLI, synthetic data, MCP smoke test, demo seeding |
| `tests/` | physics + integration tests |
| `docs/ARCHITECTURE.md` | idea, architecture, physics, maths, logic, papers, data, design, phases |
| `docs/CHECKLIST.md` | everything left to do before submitting |
| `docs/SUBMISSION.md` | Devpost text, video script, feedback, friction log |

## Validation (synthetic ground truth, through H.264)

| Clip | Truth | Tidal |
|---|---|---|
| typical sleeper + 12 s breath-hold | 15.0 /min | 14.9, pause found |
| IR night vision + roll-over | 12.25 | 12.24 |
| slow / fast | 8.75 / 25.0 | 8.77 / 25.01 |
| faint (0.15 px motion) | 15.0 | 14.9 |
| empty room / codec-starved | — | correctly "no clear reading" |

## License
MIT — see `LICENSE`.
