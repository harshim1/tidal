# Submission kit

## 1. Devpost — project description

**Tidal — contactless breathing for the people you love, from the Ring camera you already own.**

**Inspiration.** Millions of adult children lie awake wondering whether a parent living
alone is OK. Wearables get left on the nightstand. A camera feed of someone asleep looks
frozen and tells you nothing. But a sleeping chest still moves a few millimetres every
breath — a fraction of a pixel on a home camera. Invisible to people; measurable by physics.

**What it does.** Tidal connects to a Ring camera through the Ring Partner API. When
Ring reports motion — and on a schedule through the night, because a still sleeper never
triggers motion — Tidal pulls a minute of video and measures:
- breathing rate, from sub-pixel chest motion,
- moments when breathing motion goes quiet, or the rate leaves the usual resting range,
- (experimental) pulse from skin colour, when the camera is close and in colour.

Family members see it on a calm dashboard and can ask **Alexa+**: *"How did Dad
sleep?"* — "I checked on Dad 22 times. Breathing averaged about 12 breaths a minute.
One thing to know: at 10:55 PM, breathing motion went quiet for about 9 seconds."

**How it works.**
- *Physics:* brightness at a fixed pixel changes in proportion to sub-pixel motion
  times the local image gradient (first-order Taylor / brightness constancy). That is
  the principle behind MIT's Eulerian Video Magnification, which Tidal implements to
  make the motion visible, and behind its estimator.
- *Signal chain:* block pooling → illumination removal → H.264 skip-block repair →
  smoothness-priors detrending → zero-phase band-pass → automatic ROI by spectral SNR,
  contrast and spatial coherence → PCA → interpolated FFT → confidence model that
  **refuses to report** when the signal isn't trustworthy. Pulse uses the POS algorithm.
- *Ring:* HMAC-verified, de-duplicated webhooks; clip download; WHEP live video for
  night check-ins; developed and tested against the ring-sandbox emulator.
- *Alexa+:* a self-hosted MCP server (Streamable HTTP, protocol 2025-11-25) with
  `get_night_summary`, `get_latest_vitals`, `list_attention_events`, `check_in_now`,
  `get_breathing_trend`.
- *AWS:* Amazon Bedrock (Converse API) phrases the summary from deterministic numbers;
  it never invents measurements.

**Validation.** Synthetic ground truth with known physiology, through real H.264:
breathing within 0.2 /min across 8.8–25 /min, including 0.15-pixel motion and IR night
vision; breath-hold and roll-over detected; empty-room and codec-starved clips correctly
refused. [Add your own real-recording results here.]

**Challenges.** Our first estimator confidently "measured" the video encoder: heavy
H.264 compression of a static night scene skips most macroblocks, and the periodic
refresh looked like 13 breaths a minute. We added skip-block reconstruction and a
confidence model (window agreement × spatial coherence × codec update rate). Tidal now
says "no clear reading" instead of guessing.

**What's next.** Real-home beta with a reference band, Ring App Store certification,
per-person ROIs for shared beds, phase-based magnification, senior-living pilots.

**Not a medical device.** Tidal is a wellness awareness tool.

## 2. Track & mini-challenges
- Primary: **Ring** (caretaking, accessibility)
- Mini: **AWS Builder** (Bedrock Converse; hosting plan: Lambda + SQS + Fargate + DynamoDB + S3)
- Mini: **Open Source** (`tidal-vitals` library, MIT)

## 3. Demo video script (target 2:40, under the 3:00 limit)

Everything on screen is real and already in the repo: the hosted dashboard
(https://tidal-su02.onrender.com), the side-by-side render (`media/demo_side_by_side.mp4`),
`scripts/demo_flow.py` and `scripts/mcp_smoke.py`. The sleeper is synthetic footage with a
known breathing rate, so the script says "imagine" rather than claiming a real parent.

| Time | On screen | Voice-over |
|---|---|---|
| 0:00–0:12 | `demo_side_by_side.mp4`, **left half only** (crop): a still figure under a blanket. | "Imagine this is a Ring camera in your dad's bedroom. He lives alone, it's 2 a.m., and he's asleep. Is he breathing? From this picture, you can't tell." |
| 0:12–0:27 | Same clip, full width: the magnified right half rises and falls; the breathing trace and rate underneath. | "Tidal can. Each breath moves his chest a few millimetres — less than a pixel on a home camera. Tidal measures that motion, and here it magnifies it so you can see it." |
| 0:27–0:45 | Hosted dashboard, top half: the breathing orb, the *Tonight* sentence, the stats. | "Wearables get forgotten, uncharged, or refused. Tidal needs no new hardware — it works on the Ring camera already in the room. This is what the family sees in the morning: one calm answer." |
| 0:45–1:00 | Scroll to *Overnight* and *Check-ins*; point at the amber dot. | "Through the night it checks in every half hour. Most check-ins are steady. One moment is worth a look: breathing motion went quiet for about nine seconds. Amber, never red — Tidal doesn't diagnose." |
| 1:00–1:30 | Terminal: `python scripts/demo_flow.py`. Let it run: camera added → signed motion event → reading → Alexa+ answers. | "Under the hood: Ring sends Tidal a signed webhook when there's motion. Tidal verifies the signature, downloads the clip and measures it. But a sleeping person doesn't trigger motion — so at night Tidal also opens Ring's live video on a schedule and checks in by itself." |
| 1:30–1:50 | Terminal: `mcp_smoke.py` against the hosted `/mcp`; the spoken answers print. | "And nobody has to open an app. Tidal is an Alexa+ integration — ask 'How did Dad sleep?' and Alexa answers with the same sentence the dashboard shows." |
| 1:50–2:15 | One slide: pixel → brightness change → magnify → filter to breathing band → combine regions → frequency. Then the validation table from the README. | "The physics is MIT's Eulerian Video Magnification and remote-sensing research. We validated it on footage with a known breathing rate, through real video compression: within a fifth of a breath per minute, even at 0.15-pixel motion and in infrared night vision." |
| 2:15–2:30 | Validation table: highlight *empty room → no reading*. | "Just as important: when it can't see clearly — an empty bed, heavy compression — it says 'no clear reading' instead of guessing. Our first version got fooled by the video encoder; that's why the confidence check exists." |
| 2:30–2:40 | Title card: Tidal logo, "Ring · Alexa+ · AWS", GitHub URL, "Not a medical device." | "Tidal. Peace of mind from the camera you already own. It's a wellness tool, not a medical device — and it's open source." |

**Recording notes**
- Open the hosted dashboard a minute before recording: the free server sleeps when idle.
- Record the screen at 1080p, the voice separately, and add captions. No music, or only
  royalty-free music you have rights to.
- Hide the token in the `mcp_smoke.py` shot: set `TIDAL_MCP_TOKEN` before you start
  recording so it never appears on screen.

## 4. Product feedback (fill in from your own experience; starters below)

**Ring Partner API (Amazon Vision)** — *Used for:* device discovery, motion webhooks,
clip download, WHEP live video. *Worked well:* JSON:API shape is consistent; HMAC over
raw body is the right design; clip partial-content semantics are clear.
*Needs work:* no official SDK or local simulator; Playground tokens are short-lived;
the Playground doesn't deliver webhooks; no documented way to request periodic
low-bitrate snapshots for still-scene monitoring (a still sleeper never triggers
motion). *Would build again:* yes.

**ring-sandbox (community emulator)** — made offline development possible; serving a
real MP4 via `--media-dir` let us run the full loop with real footage. Gap: no WHEP media.

**Alexa+ / MCP** — Streamable HTTP and the 2025-11-25 spec worked with the official
Python SDK (v2: `MCPServer`). Would love: a documented Alexa+ test console for
self-hosted MCP servers, guidance on voice-friendly tool output, account-linking examples.

**Amazon Bedrock** — Converse API is uniform across models; we use it only to phrase
numbers. Friction: model access must be requested per region before the first call.

## 5. Feature requests
| Request | Why | Urgency |
|---|---|---|
| Scheduled low-rate frame/clip capture via the Ring API | Monitoring still scenes (sleep, caretaking) without motion triggers | Critical |
| Official Ring simulator with media (clips + WHEP) | Offline dev, CI, hackathons | Important |
| Webhook replay/test delivery from the Playground | Verifying signature handling | Important |
| Codec/bitrate hints in clip metadata | Lets analysis apps judge signal quality | Nice-to-have |

## 6. Friction log template (copy per entry)
```
### <short title>
- Task attempted:
- Steps taken:
- Expected:
- Actual:
- Severity: blocker / major / minor
- Workaround:
- Suggestion:
```

Pre-filled entries from building this repo (verify/edit to match your own run):

### Off-origin media allowlist rejects plain host names
- Task: point the Ring client at the local emulator.
- Expected: `media_origins=["127.0.0.1"]` accepted. Actual: `ValueError: invalid URL`, then
  "off-origin media downloads require HTTPS" for an http origin.
- Severity: minor. Workaround: empty allowlist for the emulator (media is same-origin).
- Suggestion: document the allowlist format and same-origin behaviour in the README.

### MCP Python SDK v2 renamed FastMCP
- Task: create an MCP server. Actual: `mcp.server.fastmcp` raises a migration error in v2.
- Severity: minor. Workaround: `from mcp.server.mcpserver import MCPServer`.
- Suggestion: Alexa+ docs/samples should pin or show the v2 import.

### Compressed static scenes defeat naive motion analysis
- Task: measure breathing from H.264 night footage.
- Actual: encoder skip blocks produced a periodic refresh artefact read as ~13 /min.
- Severity: major (silent wrong answer). Workaround: skip-block reconstruction + confidence gate.
- Suggestion: expose encoder settings / keyframe interval in Ring clip metadata.
