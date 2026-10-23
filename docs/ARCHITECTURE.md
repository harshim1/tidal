# Tidal — Architecture, Science & Plan

> **Tidal turns any Ring camera into a contactless breathing monitor.** It uses
> video-magnification physics to see chest motion too small for the eye, so a
> family can check on an aging parent overnight without a wearable to charge,
> forget, or refuse to wear.

Contents
1. The idea (problem, user, why now, why Ring)
2. System architecture
3. The physics
4. The maths
5. The logic (decision rules)
6. Research foundations (papers)
7. Data & validation
8. Design
9. Phases → launch
10. Risks, ethics, honesty

---

## 1. The idea

**Name.** *Tidal* — breathing is a tide: a slow, regular rise and fall. The wordmark
is two waves; the dashboard's orb literally breathes at the measured rate.

**The user.** The adult child who lives 40 minutes (or 4 states) away from a parent
living alone. Their 2 a.m. question is always the same: *is Mom OK?* Today they have
two bad options: a wearable (needs charging, gets taken off, is refused) or a
camera feed (invasive, and a frozen-looking image of someone asleep tells you nothing).

**The insight.** A sleeping person's chest moves 2–10 mm per breath. On a home camera
that is a *fraction of a pixel* — invisible to people, but a measurable, periodic
signal to physics. Millions of homes already have a Ring camera pointed at a room.
Tidal adds a capability those cameras have never had, with zero new hardware.

**Why Ring.** Ring's Partner API (Amazon Vision) gives event webhooks, recorded clips
and WHEP live video; Ring's priority categories include *caretaking* and
*accessibility*. Alexa+ is where the family already asks questions out loud. AWS
phrases the summary. The hackathon's own headline — "connect the living room, the
front door, and the cloud" — is literally the product.

**What it is not.** A medical device. It never diagnoses. It reports breathing rate,
regularity, and *moments worth a look*, and it refuses to give a number when the
signal isn't trustworthy.

---

## 2. System architecture

```
   ┌──────────────┐  motion webhook (HMAC-SHA256 signed, v1.1)   ┌─────────────────────────────┐
   │ Ring camera  │ ───────────────────────────────────────────▶ │ Tidal API  (FastAPI)        │
   │ (Indoor Cam, │                                              │  /webhooks/ring             │
   │  Stick Up…)  │ ◀─── clip download  /media/video/download ── │   verify → dedupe → queue   │
   │              │ ◀─── live video     WHEP (WebRTC, aiortc) ── │  scheduled night check-ins  │
   └──────────────┘                                              └──────────────┬──────────────┘
         ▲  (offline: ring-sandbox emulator, same REST + signed webhooks)        │ frames
         │                                                                       ▼
                                                               ┌─────────────────────────────┐
                                                               │ tidal_vitals (open source)  │
                                                               │  blocks → illum. removal    │
                                                               │  → codec-skip repair        │
                                                               │  → detrend → band-pass      │
                                                               │  → auto-ROI + PCA           │
                                                               │  → FFT rate, confidence     │
                                                               │  → POS pulse (experimental) │
                                                               │  → EVM render               │
                                                               └──────────────┬──────────────┘
                                                                              ▼
   ┌───────────────────┐   MCP / Streamable HTTP (2025-11-25)  ┌─────────────────────────────┐
   │ Alexa+            │ ◀──────────────────────────────────── │ Tidal MCP server (:8001/mcp)│
   │ "How did Dad      │   get_night_summary, get_latest_vitals│  tools read the store       │
   │  sleep?"          │   list_attention_events, check_in_now │                             │
   └───────────────────┘                                       └──────────────┬──────────────┘
   ┌───────────────────┐   REST + /media                       ┌──────────────▼──────────────┐
   │ Caregiver web app │ ◀──────────────────────────────────── │ SQLite (→ DynamoDB)         │
   └───────────────────┘                                       └──────────────┬──────────────┘
                                                               ┌──────────────▼──────────────┐
                                                               │ Amazon Bedrock (Converse)   │
                                                               │ phrases the numbers only    │
                                                               └─────────────────────────────┘
```

### Components (all in this repo)

| Path | Role |
|---|---|
| `tidal_vitals/` | **The open-source library** (Open Source mini-challenge). Pure numpy/scipy/OpenCV. |
| `tidal_vitals/signal.py` | detrending, band-pass, interpolated FFT peak, SNR, envelopes |
| `tidal_vitals/blocks.py` | spatial pooling, gross-motion detector |
| `tidal_vitals/breathing.py` | respiration estimator + confidence model |
| `tidal_vitals/rppg.py` | POS pulse estimator |
| `tidal_vitals/evm.py` | streaming Eulerian motion magnification |
| `tidal_vitals/render.py` | side-by-side demo render with live trace |
| `tidal_vitals/synth.py` | ground-truth synthetic video generator |
| `app/ring.py` | Ring Partner API: devices, clips, WHEP live sampling |
| `app/server.py` | webhook receiver, scheduler, REST, dashboard |
| `app/mcp_server.py` | Alexa+ MCP server (Streamable HTTP) |
| `app/summary.py` | deterministic aggregation + Bedrock phrasing + template fallback |
| `app/store.py` | SQLite persistence and webhook de-duplication |
| `app/static/index.html` | caregiver dashboard (no build step) |

### Two capture modes — and why both exist

1. **Event mode (clip).** Ring fires `motion_detected` when someone gets into bed or
   moves. Tidal downloads the clip after motion settles and measures it.
2. **Check-in mode (live).** *The key insight:* a still, sleeping person never
   triggers motion detection — which is precisely when you want to know they are
   breathing. So during the night window Tidal opens a WHEP live session every N
   minutes for ~60 s. (`TIDAL_CHECKIN_MINUTES`, `TIDAL_NIGHT_START/END`.)

### Security & reliability decisions (what Ring's engineers will look for)

- Webhook signature is verified over the **raw bytes** (HMAC-SHA256, `X-Signature`),
  with a max-age check — never over re-serialised JSON.
- Ring delivers webhooks at-least-once: request ids are de-duplicated in the store.
- The webhook returns 2xx immediately; analysis runs in a background task.
- Clip downloads follow Ring's 303 → pre-signed URL flow with an origin allowlist;
  bearer tokens are never forwarded to the media host.
- Partial clips (206) are accepted and the actual length is used.
- Video never leaves the processing step except as the optional magnified render;
  only numbers reach Alexa and Bedrock.

---

## 3. The physics

### 3.1 Why sub-pixel motion is measurable at all

Model one image row as a pattern moving by a small displacement δ(t):

  I(x, t) = f(x + δ(t))

First-order Taylor expansion (valid while δ is small relative to the texture's
spatial wavelength λ):

  I(x, t) ≈ f(x) + δ(t) · ∂f/∂x

So **each pixel's brightness is a scaled copy of the displacement**, with gain equal
to the local image gradient ∂f/∂x. On a textured blanket the gradient is large, so a
0.3-pixel motion becomes a few grey levels of change — well above what averaging can
dig out of sensor noise. On a flat, untextured surface the gain is ≈ 0, which is why
Tidal weights blocks by contrast.

This one equation is the foundation of both halves of Tidal:
- the **estimator** reads δ(t) from intensity changes (Section 4.2),
- the **magnifier** adds α·δ(t)·∂f/∂x back to the image, producing
  f(x + (1+α)δ(t)) — the chest visibly rising and falling (Section 3.3).

### 3.2 Breathing mechanics

Tidal volume at rest ≈ 0.5 L; anterior chest-wall excursion ≈ 2–10 mm (more under a
duvet, which amplifies and spreads it). A ceiling- or shelf-mounted indoor camera at
2–3 m with ~110–140° FOV covers ~1–2 cm per pixel at 320 px width, ~2–4 mm per pixel
at 1080p. So the signal is **0.1–1 px at analysis resolution** — exactly the regime
where the linear model above holds and EVM was designed to operate.

Respiratory band: 0.1–0.7 Hz (6–42 breaths/min). Adult resting range is typically
12–20 /min; Tidal uses 10–22 only to colour the UI (*not* a clinical threshold).

### 3.3 Eulerian Video Magnification (EVM)

Lagrangian methods track features; EVM instead watches each **fixed pixel** over time
("Eulerian", like a fluid sensor at a fixed point). Steps:

1. **Spatial decomposition** — Laplacian pyramid: each level is a band of spatial
   frequencies with representative wavelength λ_l = λ_0 / 2^l.
2. **Temporal filtering** — band-pass each pixel of each level to the breathing
   band. Tidal uses the paper's real-time form: difference of two first-order
   low-passes, `lp_k ← (1−r_k)·lp_k + r_k·x`, `r_k = 1 − exp(−2π f_k / fs)`.
3. **Amplification** — multiply by α_l, where α is limited by the validity
   condition **(1+α)·δ(t) < λ/8**: fine levels (small λ) get small α, the
   finest and coarsest levels get 0. Exaggeration factor 2 as in the reference code.
4. **Reconstruct** — collapse the pyramid, add to the original (in YIQ, chroma
   attenuated so colours don't swim), convert back to RGB.

Measured on the synthetic sleeper: chest displacement 0.39 px peak-to-peak becomes
~5 px at α=30, λ_c=40 (≈13× visible gain) without visible artefacts; α≥60 starts
amplifying sensor noise into shimmer — the classic EVM noise/α trade-off.

### 3.4 Remote photoplethysmography (pulse)

Each heartbeat fills facial capillaries; oxy-haemoglobin absorbs most strongly in the
green band, so skin reflectance oscillates by ~0.1–1 % at the heart rate. Specular
reflection and motion add large, colour-neutral changes. **POS** projects normalised
RGB onto the plane orthogonal to the skin-tone vector, where the pulse survives and
intensity/specular changes cancel (Section 4.4).

**Physics limit Tidal respects:** at 0.4 % amplitude the pulse is smaller than an H.264
quantisation step and is destroyed by compression; at ≥0.8 % with good encoding it is
recovered to ±1 bpm. Ring night vision is monochrome IR, so POS (which needs colour)
cannot work at night at all. Pulse is therefore labelled *experimental* and only
shown when its own confidence passes.

---

## 4. The maths

### 4.1 Spatial pooling
Average 8×8 blocks: for spatially correlated signal and independent per-pixel noise σ,
SNR improves by √64 = 8×.

### 4.2 Illumination removal
Model I(x,t) = L(t)·R(x,t). Convert each block to relative units
s_b(t) = I_b(t)/Ī_b − 1, estimate the global light level as the per-frame
**median over blocks** ℓ(t) = median_b s_b(t), subtract it. The median is robust as long
as the breathing surface covers < 50 % of the frame, and it cancels auto-exposure
pumping and slow daylight drift that otherwise masquerade as breathing.

### 4.3 Codec-skip repair
H.264 sends unchanged ("skip") macroblocks for static regions, turning a smooth signal
into an irregular staircase. For each block Tidal keeps only frames where the value
changed and linearly interpolates between them. The share of updated frames is kept as
`codec_update_fraction`; the confidence ramps down from 1 at 0.7 to 0 at 0.2 (Section 5).

### 4.4 Detrending (Tarvainen smoothness priors)
ẑ = (I − (I + λ²D₂ᵀD₂)⁻¹) z, D₂ the second-difference matrix. Removes residual drift
without the phase distortion of an IIR high-pass. λ = 20·fs.

### 4.5 Band-pass
3rd-order Butterworth in second-order sections, applied forward-backward (sosfiltfilt):
zero phase, so the waveform's peaks line up with real inhalations.

### 4.6 Automatic ROI + PCA
Score each block: SNR_dB + 3·log₁₀(energy/median) + 2·log₁₀(contrast/median).
Take the top 8 %, stack their normalised series X (k × T) and take the first right
singular vector of X = UΣVᵀ — the dominant shared waveform; PCA absorbs the unknown
± sign of each block's gradient gain. **Second pass:** every block with
|corr(block, wave)| ≥ 0.6 joins the ROI (sign-aligned), the waveform is re-averaged.
No body or face detector is needed — it works under duvets and in IR night vision.

### 4.7 Rate estimation
Hann window, zero-padded FFT (≥4096 bins), peak in band, **parabolic interpolation on
log-power** for sub-bin accuracy:
f̂ = f_k + ½(a − c)/(a − 2b + c)·Δf.
SNR (de Haan & Jeanne): power within ±0.06 Hz of f̂ and 2f̂ over remaining in-band power.
Per-window quality q = 1/(1 + e^(−SNR/2)). Windows: 30 s, step 5 s.

### 4.8 POS
For each 1.6 s window: Cn = C/mean(C); S = [[0,1,−1],[−2,1,1]]·Cn;
h = S₁ + (σ(S₁)/σ(S₂))·S₂; overlap-add. Band-pass 0.7–3 Hz, keep the 8 skin blocks
(YCrCb skin locus) with the highest SNR, average.

### 4.9 Breath-hold / pause detection
Hilbert envelope of the waveform, 2 s moving average; intervals where it stays below
0.3 × its median for ≥ 8 s are "breathing motion paused". Gross-motion frames are
excluded so moving isn't mistaken for stillness.

### 4.10 Gross motion
Fraction of pixels (after 3×3 blur) whose value changed by > 8 grey levels versus the
frame 0.5 s earlier; > 2 % → motion, padded ±2 s. Breathing changes pixels by a few
levels; rolling over changes thousands by tens.

---

## 5. The logic (decision rules)

**Confidence** (0–1) = mean window quality
× (0.4 + 0.6·agreement)        — share of windows within ±2 /min of the median
× clip((spatial − 0.3)/0.4)      — one contiguous moving surface (8-connected ROI)
× clip((codec − 0.2)/0.5)        — footage the codec actually updated

**Report a number only if confidence ≥ 0.6.** Otherwise: "no clear reading".
On a sweep of 6 seeds per scenario through H.264 (48 clips) every true signal scores
≥ 0.8 and every empty-room or codec-starved clip ≤ 0.35 — the threshold sits in the gap.
An earlier step penalty (×0.6 below 0.35 codec updates) let 3 of 6 empty rooms report a
rate at 0.73–0.96: their encoder-held wall blocks updated on 35–39 % of frames, just
above the cliff. True signals update on ≥ 73 % of frames, so a ramp separates them cleanly.

**Status**
| Status | Rule | Caregiver copy |
|---|---|---|
| `restless` | gross motion > 50 % of clip | "Lots of movement — couldn't measure reliably." |
| `no_signal` | confidence < 0.6 | "No clear breathing rhythm in view." |
| `attention` | any pause ≥ 8 s, or rate outside 10–22 /min | "Breathing motion went quiet for about N seconds." |
| `steady` | otherwise | "Breathing is regular." |

**Language rules** (enforced in the Bedrock system prompt and the template): plain
words, times in the listener's time zone, never "apnea", never a diagnosis, Bedrock
may only phrase numbers it was given.

---

## 6. Research foundations

| # | Paper | Used for |
|---|---|---|
| 1 | Wu, Rubinstein, Shih, Guttag, Durand, Freeman. *Eulerian Video Magnification for Revealing Subtle Changes in the World.* ACM TOG (SIGGRAPH) 2012. | Linear EVM, α/λ_c bound, IIR temporal filter, YIQ |
| 2 | Wadhwa, Rubinstein, Durand, Freeman. *Phase-Based Video Motion Processing.* SIGGRAPH 2013. | Phase-based upgrade path (less noise at high α) |
| 3 | Wang, den Brinker, Stuijk, de Haan. *Algorithmic Principles of Remote PPG.* IEEE TBME 2017. | POS pulse algorithm |
| 4 | de Haan, Jeanne. *Robust Pulse Rate from Chrominance-Based rPPG.* IEEE TBME 2013. | CHROM; spectral SNR metric |
| 5 | Verkruysse, Svaasand, Nelson. *Remote plethysmographic imaging using ambient light.* Optics Express 2008. | Proof that consumer cameras see the pulse; green channel |
| 6 | Poh, McDuff, Picard. *Non-contact, automated cardiac pulse measurements using video imaging and blind source separation.* Optics Express 2010. | Source separation framing; motivates PCA step |
| 7 | Tarvainen, Ranta-aho, Karjalainen. *An advanced detrending method with application to HRV analysis.* IEEE TBME 2002. | Smoothness-priors detrending |
| 8 | Horn, Schunck. *Determining Optical Flow.* Artificial Intelligence 1981. | Brightness-constancy / first-order model |
| 9 | Massaroni et al. *Contactless Methods for Measuring Respiratory Rate: A Review.* IEEE Sensors J. 2021. | Landscape of camera/radar respiration methods |
| 10 | Bobbia et al. *Unsupervised skin tissue segmentation for remote PPG (UBFC-rPPG dataset).* Pattern Recognition Letters 2019. | Public validation data for pulse |
| 11 | Stricker, Müller, Gross. *Non-contact video-based pulse rate measurement on a mobile service robot (PURE dataset).* IEEE RO-MAN 2014. | Public validation data |
| 12 | Liu, McDuff et al. *rPPG-Toolbox: Deep Remote PPG Toolbox.* NeurIPS Datasets & Benchmarks 2023. | Benchmark harness; learned-model upgrade path |

Prior art / market proof: camera rPPG companies (Binah.ai, Nuralogix), radar sleep
sensing (Google Nest Hub "Sleep Sensing" with Soli), clinical camera respiration
in NICUs. Tidal's novelty is the *distribution*: reuse cameras already in homes via
Ring's platform, answer through Alexa+.

---

## 7. Data & validation

### 7.1 Synthetic ground truth (in repo, reproducible)
`scripts/make_synthetic.py` renders a top-down sleeper under a textured blanket with
known physiology (sub-pixel chest displacement with drifting rate and optional
breath-hold, haemoglobin-weighted skin pulse, sensor noise, auto-exposure drift,
roll-over, H.264). Results (`python scripts/analyze.py data/<clip>.mp4 --truth …`):

| Clip | Truth (/min) | Tidal | Confidence | Notes |
|---|---|---|---|---|
| sleeper_day | 15.00 | 14.92 | 0.98 | breath-hold 52–64 s found at 53–63 s |
| sleeper_night_ir | 12.25 | 12.24 | 0.91 | monochrome IR; roll-over excluded |
| sleeper_slow | 8.75 | 8.77 | 1.00 | flagged "outside usual range" |
| sleeper_fast | 25.00 | 25.01 | 1.00 | pulse 91.8 vs 92 (1 % amplitude) |
| sleeper_faint | 15.00 | 14.90 | 1.00 | only 0.15 px motion, σ=3 noise |
| empty_room | — | **no reading** | 0.18 | correct refusal |
| codec_starved | — | **no reading** | 0.00 | correct refusal (3 % frames updated) |

**Finding worth telling the Ring team:** heavy compression of a static night scene
makes the encoder skip most macroblocks; the first version of the estimator then
"measured" the encoder's refresh cycle (~13 /min) as breathing. The codec-skip repair
and confidence model came directly out of that failure — see the friction log.

### 7.2 Real-world protocol (do this with your own recordings)
1. Phone or Ring Indoor Cam on a shelf, 1.5–3 m, angled down at the bed.
2. Lie still 2–3 minutes; count breaths out loud in 30 s blocks *or* wear any
   chest-strap/fitness band for reference; note the count.
3. Record variants: blanket on/off, lights on, IR night mode, side/back sleeping, one
   deliberate 15 s breath-hold, one roll-over, empty bed.
4. `python scripts/analyze.py clip.mp4 --render clip_render.mp4` → compare.
5. Report mean absolute error and the % of clips where Tidal correctly refused.

### 7.3 Public datasets for the pulse path
UBFC-rPPG, PURE (face video + contact PPG). For respiration, any dataset with
reference belts (e.g. sleep-lab PSG video) under appropriate licence.

---

## 8. Design

**Principles.** One user (the remote caregiver), one question ("is she OK?"), one
answer in one glance. Calm by default; colour only when something deserves it.
Honest uncertainty: "no clear reading" is a first-class state, not an error.

**Visual language.** Night palette (#0b111a) because the product is used at night;
teal "tide" (#5ecbd6) for normal; warm amber (#f5b05c) for "worth a look" — never
red, because nothing Tidal shows is an emergency verdict. Fraunces (serif) for the
spoken summary and big numbers — it reads like a person talking; IBM Plex for data.

**Signature elements.**
- *The breathing orb* — concentric rings animate at exactly 60/rate seconds per
  cycle, so the screen breathes with Dad. Freezes amber during a pause. Respects
  `prefers-reduced-motion`.
- *"What the camera sees / What Tidal sees"* — side-by-side magnified clip with the
  measured trace underneath. This is the demo's wow shot and the trust builder.
- *Tonight* — the exact sentence Alexa+ speaks, so web and voice never disagree.
- *Overnight* — dots against the shaded usual range; amber for moments to note.

**Voice (Alexa+).** 2–4 short sentences, times in the listener's zone, "about 15
breaths a minute", always ends with the last check time. Never medical vocabulary.

---

## 9. Phases → launch

| Phase | Goal | Exit criteria | Status |
|---|---|---|---|
| **P0 Foundations** | Signal chain proven on ground truth | All synthetic clips within ±0.3 /min; empty/starved refused; 15 tests green | ✅ in repo |
| **P1 Ring loop** | Webhook → clip → reading | Signed emulator event produces a stored reading end-to-end | ✅ `scripts/demo_flow.py` |
| **P2 Voice** | Alexa+ MCP server | Streamable HTTP, protocol 2025-11-25, tools return `speech` | ✅ verified with MCP client |
| **P3 Product** | Caregiver dashboard + magnified render | Readable at 390 px and desktop; upload-your-own-video | ✅ |
| **P4 Real footage** | Your own Ring/phone recordings | MAE < 1 /min on ≥ 10 clips; correct refusals | ▶ next (Section 7.2) |
| **P5 Hackathon submission** | Video, Devpost, feedback, friction log | See `docs/CHECKLIST.md` | ▶ |
| **P6 Private beta** | 10–20 families with a Ring Indoor Cam | Weekly MAE vs. a reference band; caregiver NPS; false-alert rate | — |
| **P7 Ring App Store** | Certified Ring app | Ring certification, privacy review, OAuth account linking, hosted on AWS (Lambda/Fargate + DynamoDB + S3 for renders) | — |
| **P8 Clinical track** | Senior living & RPM | IRB study vs. PSG, FDA pathway decision, HIPAA hosting | — |

**Hosting for launch (AWS).** API Gateway → Lambda for webhooks (fast 2xx), SQS →
Fargate workers for video analysis (OpenCV/ffmpeg are heavy for Lambda), DynamoDB for
readings, S3 + CloudFront for renders, Bedrock for phrasing, the MCP server on Fargate
behind an ALB with OAuth (Alexa+ account linking).

**Business model.** Consumer subscription via the Ring App Store for families; B2B per-room
licence for senior-living operators who already have cameras installed; later,
clinical remote monitoring (requires FDA clearance before any reimbursement claims).

---

## 10. Risks, ethics, honesty

| Risk | Mitigation |
|---|---|
| Over-trust ("the app said he was fine") | Wellness framing everywhere; "no clear reading" state; never says "normal" |
| Privacy of in-home video | Consent of the monitored person required in onboarding; video processed transiently, only numbers stored by default; renders opt-in and expiring |
| Compression / low light | Codec-skip repair, confidence model, refusal; recommend camera placement |
| Multiple people in bed | ROI coherence drops → confidence drops → refusal (future: per-person ROIs) |
| Pets, fans, curtains | Fans/curtains are usually outside the 0.1–0.7 Hz band or not spatially coherent with a torso; pets — future work |
| Pulse accuracy | Labelled experimental; hidden unless its own confidence ≥ 0.6; impossible in IR |
