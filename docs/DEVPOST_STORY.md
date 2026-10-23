## Inspiration

Millions of adult children lie awake wondering whether a parent who lives alone is OK. The usual answers don't work at 2 a.m.: wearables get left on the nightstand, go uncharged, or are simply refused, and a camera feed of someone asleep looks frozen — it tells you nothing.

But a sleeping chest still moves a few millimetres with every breath. On a home camera that's a *fraction of a pixel* — invisible to people, measurable by physics. Millions of homes already have a Ring camera in the room. We wanted to give those cameras a capability they've never had, with zero new hardware.

## What it does

Tidal turns a Ring camera into a contactless breathing monitor for caregivers:

- **Breathing rate** from sub-pixel chest motion, through blankets and in infrared night vision.
- **Moments worth a look** — breathing motion going quiet, or a rate outside the usual resting range — shown in amber, never red. Tidal never diagnoses.
- **Honest refusals:** when the signal isn't trustworthy (empty bed, heavy compression, lots of movement) it says *"no clear reading"* instead of guessing.
- **One calm answer** on a dashboard, and by voice: *"How did Dad sleep?"* → *"I checked on Dad 22 times. Breathing averaged about 12 breaths a minute. One thing to know: at 3:31 AM, breathing motion went quiet for about 9 seconds."*

## How we built it

**The physics.** Model a row of pixels as a texture moving by a tiny displacement $\delta(t)$. To first order,

$$I(x,t) = f\big(x+\delta(t)\big) \approx f(x) + \delta(t)\,\frac{\partial f}{\partial x}$$

so every pixel's brightness is a scaled copy of the chest motion, with gain equal to the local image gradient. That single equation drives both halves of Tidal: the **estimator** reads $\delta(t)$ back out of brightness changes, and the **magnifier** (MIT's Eulerian Video Magnification) adds $\alpha\,\delta(t)\,\partial f/\partial x$ back in so you can *see* the chest rise and fall.

**The signal chain** (open-source `tidal_vitals`, pure NumPy/SciPy/OpenCV):
8×8 block pooling → illumination removal (per-frame median) → H.264 skip-block repair → smoothness-priors detrending → zero-phase Butterworth band-pass (0.1–0.7 Hz) → automatic chest ROI by spectral SNR, contrast and spatial coherence → PCA → windowed FFT with parabolic peak interpolation → a confidence model that only reports a number at $\geq 0.6$. No body or face detector needed. Pulse (POS rPPG) is included as experimental and hidden unless its own confidence passes.

**Ring.** HMAC-SHA256-verified, de-duplicated motion webhooks; clip download through Ring's pre-signed redirect flow; and scheduled live check-ins over WHEP — because a still, sleeping person never triggers motion detection, which is exactly when you want to know they're breathing. Developed end to end against the ring-sandbox emulator.

**Alexa+.** Tidal ships an MCP server (Streamable HTTP, protocol 2025-11-25) with `get_night_summary`, `get_latest_vitals`, `list_attention_events`, `get_breathing_trend` and `check_in_now`. Every tool returns a `speech` field written for voice, identical to the dashboard sentence.

**AWS.** Amazon Bedrock (Converse API) can phrase the nightly summary — it only ever phrases numbers computed deterministically, with a template fallback.

**Hosting.** One Docker service on Render serving the dashboard, API, webhook and MCP endpoint. It fails closed: in public mode it won't start without its secrets, webhooks are rejected without a signing key, and the MCP endpoint requires a bearer token.

## Challenges we ran into

- **We measured the video encoder.** Our first estimator confidently reported ~13 breaths a minute from an empty, heavily compressed night scene. H.264 skips unchanged macroblocks and refreshes them periodically — a perfect fake rhythm. We added skip-block reconstruction and a codec-aware confidence model.
- **Then it fooled us again.** Testing 48 freshly rendered clips, 3 of 6 empty rooms still slipped past a hard cutoff and reported a rate. Replacing the cliff with a continuous penalty separated them cleanly: every true signal $\geq 0.81$, every empty or codec-starved clip $\leq 0.33$.
- **Memory.** A 30-second analysis peaked at 2.4 GB. Chunked float conversion, frame-by-frame motion detection and a streamed render cut that ~4× with identical results.

## Accomplishments that we're proud of

On ground-truth video with known physiology, through real H.264: breathing within **0.2 breaths/min** from 8.8 to 25 /min, including **0.15-pixel** motion and infrared night vision; the 12-second breath-hold detected; roll-overs excluded; and empty-room and codec-starved clips correctly refused. Twenty-two automated tests cover the physics, the Ring webhook security, the MCP tools and the hosted demo's access rules.

## What we learned

The hard part of contactless sensing isn't finding a signal — it's knowing when the signal you found is fake. Refusing to answer is a feature.

## What's next for Tidal

A real-home beta against a reference breathing band, Ring App Store certification and OAuth account linking, per-person ROIs for shared beds, phase-based magnification for less noise, and senior-living pilots.

*Tidal is a wellness awareness tool, not a medical device. The public demo uses synthetic footage with a known breathing rate.*
