# Tidal — Task checklist

Deadline: **Oct 23, 2026, 12:00 PM PDT (3:00 PM EDT)**. Submit by 2:00 PM EDT.

## A. Already done in this repo
- [x] Signal library `tidal_vitals` (EVM, breathing, POS pulse, confidence model)
- [x] Synthetic ground-truth dataset + scoring CLI
- [x] Ring Partner API integration (webhook HMAC + dedupe, clip download, WHEP live)
- [x] Offline Ring emulator flow (`scripts/demo_flow.py`)
- [x] Alexa+ MCP server, Streamable HTTP, protocol 2025-11-25, 5 tools
- [x] Amazon Bedrock phrasing with deterministic fallback
- [x] Caregiver dashboard (desktop + phone), upload-your-own-video
- [x] 15 automated tests passing
- [x] Architecture / maths / physics / papers / design docs

## B. Setup (15 min)
- [ ] `pip install -r requirements.txt` (and `ffmpeg` on PATH)
- [ ] `python scripts/make_synthetic.py && python -m pytest`
- [ ] `python scripts/demo_flow.py --keep` → open http://127.0.0.1:8000
- [ ] Register on Devpost; create a free Ring developer account; get a Playground token + HMAC key
- [ ] Create `FRICTION_LOG.md` from `docs/SUBMISSION.md` template and log as you go

## C. Real data (the step that wins credibility)
- [ ] Record 3+ clips of yourself lying still (protocol in ARCHITECTURE §7.2), one with a 15 s breath-hold
- [ ] `python scripts/analyze.py clip.mp4 --render clip_render.mp4` — note error vs. your count
- [ ] If you own a Ring camera: set `RING_BASE_URL` to production, add your token, run the server, trigger motion, confirm a reading
- [ ] If not: run `demo_flow.py --clip your_clip.mp4` (emulator serves your real footage as the Ring recording)
- [ ] Pick the best render for the video's opening shot

## D. Optional AWS (AWS Builder mini-challenge)
- [ ] Enable a Claude model in Bedrock (us-east-1), set `TIDAL_BEDROCK=1`, `BEDROCK_MODEL_ID`
- [ ] `curl localhost:8000/api/night` → confirm `"phrased_by": "bedrock"`
- [ ] Screenshot for the feedback section

## E. Alexa+ demo
- [ ] `python scripts/mcp_smoke.py` → record terminal output
- [ ] Optional: expose `:8001/mcp` via a tunnel and connect from any MCP client (Claude, Kiro, etc.) to show natural-language questions

## F. Open Source mini-challenge
- [ ] Publish `tidal_vitals/` as its own public repo (`tidal-vitals`, MIT, `pyproject.toml` included)
- [ ] Optional PR to `ring-sandbox`: add a `sleeping_resident` scenario + note on the codec-skip finding
- [ ] Fill contribution URL, repo URL, GitHub username, description

## G. Submission package
- [ ] Demo video < 3 min (script in SUBMISSION.md), YouTube/Vimeo public, English, no copyrighted music
- [ ] Devpost description (text in SUBMISSION.md)
- [ ] Product feedback on Ring API, ring-sandbox, Alexa+ MCP, Bedrock, Amazon Devices Builder Tools
- [ ] Friction log entries (up to +10 % judging bonus)
- [ ] Track: Ring. Mini-challenges: AWS Builder, Open Source
- [ ] Repo public with LICENSE — or private + add knmeiss, giolaq, anishamalde, mosesroth, emersonsklar + testing@devpost.com
- [ ] Submit, then re-open the Devpost page and confirm every field saved
