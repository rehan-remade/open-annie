# open-annie contracts

The shared interfaces every piece is built against. Source of truth for the
architecture is the Annie Live Character review (browser-first, thin broker).

## Layout

```
stage/index.html          zero-build page, CDN import map, mock mode with no keys
packages/core/src/*.js    plain ESM (no build step) imported by stage and apps
broker/                   the one tiny broker contract
  annie_broker/           pure-python core (upstream clients, governor, budget)
  fal_app.py              fal serverless App wrapping the core (CPU machine)
  local.py                FastAPI runner of the same core for local dev
assets/avatars/<id>/      model.vrm + avatar.json (license manifest)
assets/clips/<pack>/      pack.json + <clip>.json motion files (see "Motion packs"); index.json lists installed packs
motion/blender/           offline motion pipeline: hand-keyed in headless Blender -> assets/clips/annie-blender
persona/                  character.md, voice.md
scripts/check_assets.py   stdlib-only CI license gate
demo/                     the demo: script, voice lines, live director plan, capture, mux, browser frame
```

## Broker HTTP contract (all implementations)

| Route | In | Out |
|---|---|---|
| `GET /health` | – | `{ok, version, providers: {voice: bool, decide: bool}}` |
| `POST /session` | `{sdp, voice?, instructions?}` JSON | `{sdp, session_id, expires_at, token}` — SDP answer from GPT-Live, minted with the server key. `token` is an HMAC (session_id + expiry) required by `/decide`. |
| `POST /decide` | `{token, state, questions}` (Jev `systemone` body minus `model`) | Jev response passthrough `{answers, usage}` plus `latency_ms`. 503 `{error:"decide_unavailable"}` when no key / breaker open / rate-shaped; the browser then uses its local floor. |
| `WS /decide` | same JSON per message, plus `id` | same JSON per reply, echoing `id` |
| `GET /status` | – | `{spent_today_usd, budget_usd, active_sessions, max_sessions, voice_seconds_today}` |

Upstream error bodies are never forwarded. No transcripts are retained.

## Browser core event bus

`core` modules talk through one `EventTarget`-style bus with these events:

- `user.partial {text}` / `user.final {text}` — input transcript
- `assistant.partial {text}` / `assistant.final {text}` — output transcript so far (cumulative)
- `assistant.audio.start` / `assistant.audio.end`
- `bargein {}`
- `decision {kind: "listen"|"reply", answers, latency_ms, source: "jev"|"floor"|"mock"}`
- `cue.face {name, intensity}` / `cue.clip {name, energy}` / `cue.reaction {name}`

## Providers (capability-flagged, one file each)

- Voice: `gptlive` (WebRTC via broker `/session`), `mock` (scripted audio + timed transcript deltas).
- Decision: `broker` (Jev via `/decide`), `floor` (deterministic keyword/regex rules, always on as fallback).
- Lipsync: `headaudio` (vendored MIT worklet, 15 Oculus visemes per 16 ms frame; live on the WebRTC
  track, offline over decoded clips keyed by audio time), `energy` (RMS + spectral vowel guess; loudness
  for both, and the whole mouth if HeadAudio fails to load; `?lipsync=energy` forces it). Feature frame:
  `{amp, db, vowels, visemes?: {t, p[15]}}` → `mouth.js` (`createMouth(vrm)` → `update(f, now, dt)` →
  `{aa, ih, ou, ee, oh, open, viseme}`; `apply(em, w, faceW)` writes the mouth). Measured in `lipsync-eval/`.

## Vocabularies

Faces (Jev `face` / LISTEN choice): `neutral happy joyful amused relieved sad concerned hurt angry annoyed
surprised shocked afraid curious thinking embarrassed proud tender playful skeptical sleepy bored`.
Each maps to VRM presets (happy angry sad relaxed surprised) with weights; see `core/src/faces.js`.

Clips (same names in every pack; procedural annie-core fills any a pack lacks): `idle_a idle_b idle_c wave nod headshake excited_bounce clap dance
sad_slump shrug think laugh surprised_recoil bow point_self`.

Reactions (body language): `agree disagree empathize celebrate recoil ponder bashful scoff none`.

Listen loops: `listen_a listen_b` (played while the user talks). Talk loops: `talk_{calm,animated,excited}_N`
(played by the talk layer while Annie speaks; energy gentle/moderate/high picks calm/animated/excited).

## Motion packs (`assets/clips/<pack>/`)

Motion authored offline (`motion/blender`) and baked onto three-vrm **normalized** humanoid bones in
**VRM 1.0 space** (rest = T-pose, identity rotations, facing +Z, character's left = +X). The browser loads
them with `packages/core/src/motion.js`, which applies the VRM 0.x flip (negate quaternion x and z, and
hips x/z) for 0.x rigs, exactly as `clips.js` does.

`<clip>.json`:

```
{ "fps": 30, "duration": 5.0, "loop": true, "frames": 150, "space": "vrm1-normalized",
  "bones": { "<vrmHumanBoneName>": [x, y, z, w,  x, y, z, w, ...] },   // one unit quaternion per frame (local)
  "hips_position": [x, y, z, ...],     // metres, offset from the rest hips position, one per frame
  "source": "...", "license": "...", "prompt"?: "...", "offset"?: 3.336, "energy"?: "calm" }
```

Floats are rounded to 4 decimals. `duration` is `frames/fps` for loops (frame `frames-1` flows into 0)
and `(frames-1)/fps` otherwise. Bones may be a subset: talk clips carry only the upper body
(spine..head, shoulders, arms, hands, fingers); a body clip that leaves the fingers undriven gets the
pack's relaxed `hands` pose. Finger names are VRM 1.0 (`leftThumbMetacarpal/Proximal/Distal`,
`left{Index,Middle,Ring,Little}{Proximal,Intermediate,Distal}`).

`pack.json`: `{id, format, space, fps, license, license_url, authors, sources{}, generator, description,
avatar{id, file, sha256}, hands?{bone: quat}, license_caveat?, clips: {name: {file, loop, duration, frames,
sha256, bytes, kind?: idle|listen|fidget|gesture|action, when, layer?: "talk", energy?, offset?, prompt?, audio?}}}`.
`when` is the sentence Jev uses as that clip's criteria. `kind: action` needs a spoken commitment.
A per-take pack (none ships today) adds `audio{file, sha256, session_audio_start}` and gives each turn an
`offset`, the clip start in seconds on that audio track; `Character.setTalkTrack` plays it.

`assets/clips/index.json`: `{body: [pack ids, first wins], talk: id, takes?: {audioFile: id}}`.
`scripts/check_assets.py` checks every pack: licence in the motion allowlist, one manifest entry per
file with matching sha256/bytes, well-formed quaternion tracks, and that index.json only names real packs.

## Character body API (`packages/core/src/character.js`)

`Character.load({url, scene, camera, seed, motion?: {packs, talk}})`, `playClip(name, {energy})`,
`gesture(nod|shake|tilt|duck)`, `setFace`, `setMouth(weights, speaking)`, `bargeIn()`, `update(now, dt)`,
`speaking`, `listening` (set by `annie.js` from user transcripts),
`hear(now, features, userSpeaking, userLevel)` (`stage.js`, every frame before `update`: Annie's lipsync
feature frame times the gesture strokes; the user's voice activity drives listening nods and tilts),
`transcript(text, now)` (`annie.js`, the cumulative assistant transcript: sentence ends and emphasis), and
`setTalkTrack(packUrlOrPack, audioStart, audioFile?)`: time-lock a per-take talk track to the audio
timeline (`stage.js` calls it when replaying a recorded session). `current` is the base source
`{name, rate, kind, ...}`; `guard.stats` reports the collision guard per frame.

Every source is sampled directly (no mixer) and every switch is inertialized (see "Runtime body
order"): idle / listen loops, calm-idle `kind: "fidget"` one-shots every 6-12 s, Jev-picked clips; the
talk layer (upper body, held over 0.5 s word gaps, off while a semantic clip plays) with its talk-rest
loop and gesture phrases; then procedural breathing, sway, head gestures, blink and gaze.

## Gesture phrases (co-speech, `layer: "gesture"`) — added 2026-09-29

Short upper-body phrases the runtime **gesture scheduler** plays while Annie speaks, timed so the
*stroke* lands on speech emphasis. They live in the body pack next to the other clips (`annie-blender`).

pack.json clip entry:
```
{ "file": "g_offer_r.json", "layer": "gesture", "loop": false, "duration": 1.6, "frames": 49,
  "kind": "beat|deictic|metaphoric|iconic|emblem", "hands": "left|right|both",
  "energy": "calm|animated|excited", "rest": "low|mid",        // posture it starts/ends in
  "prep_end": 0.35, "stroke": 0.55, "retract_start": 1.0,     // seconds into the clip
  "strokes"?: [0.55, 0.9, 1.2],                               // multi-stroke: every beat, ascending
  "when": "offering an idea, 'here's the thing'",             // optional; Jev may pick semantically
  "sha256": "...", "bytes": 1234 }
```
- Data: the JSON clip format above, **upper body only** (spine, chest, upperChest, neck, head,
  shoulders, arms, hands, fingers). Frame 0 and the last frame equal the talk-rest posture named by
  `rest`, so phrases chain and hand back cleanly.
- `stroke` is the frame of peak emphasis (the beat). The scheduler plays the preparation, holds at
  the pre-stroke pose (`prep_end`) until a stressed syllable of Annie's voice, then time-warps the
  stroke segment so the stroke lands on that syllable's vowel (causal: onsets from the audio as it
  plays, ~0.1-0.2 s after the syllable starts).
- `contact` (optional, `true`): the phrase touches the body or the other hand (hand on chest, clasped
  hands). The runtime plays it at its authored size; other phrases are scaled by energy
  (gentle 0.8 / moderate 1 / high 1.12), chosen once when the phrase starts.
- `strokes` (optional): a multi-stroke phrase that chains beats without returning to rest.
  `strokes[0]` equals `stroke`; each later beat holds at `strokes[k] - (stroke - prep_end)` (but after
  the previous beat) and fires on the next stressed syllable, so each beat's approach should read as
  a small preparation of that length. `retract_start` follows the last beat.
- Phrases must be collision-free against the real avatar mesh (hands/forearms never inside the
  body, clothing, thighs or hair), measured in Blender on the deformed mesh of the shipped outfit.
- Talk-rest loops (`layer: "talk"`) remain the idle posture *between* phrases while she speaks.
  Each carries `rest: "low"|"mid"` (the rest it holds: `talk_calm_*` low, animated/excited mid), and
  pack.json has a pack-level `rests: {low, mid, note}` describing both postures.
- `kind: "fidget"` clips (2.5–3.5 s one-shots: hair tuck, wrist fiddle, heel-toe rock, clasp, glance, hands on hips,
  stretch) start and end on the idle base pose; the runtime plays one every 6–12 s of idling.

## Runtime body order (character.js, per frame) — 2026-09-29

1. Base: idle / listen loop or a semantic clip, sampled directly and **inertialized** on switches
   (offset decays, no crossfade blending of two poses).
2. Talk-rest loop + gesture phrases (upper body), inertialized in and out.
3. Procedural: breathing, sway, head gestures (nod/shake/tilt), gaze.
4. **Collision guard** (`core/src/body-collide.js`): arm sample points vs polar height maps of the
   torso, hips, thighs and head built from the skinned mesh (outer surface, cloth included, plus the
   skin under it); resolve penetration by adjusting shoulder/elbow (two-bone IK), temporally smoothed.
   Hands, fingers and the wrist end of the forearm stay outside; the elbow end may rest pressed into
   loose cloth (up to 60% of its standoff from the skin, 3.5 cm). Always last.
5. `stepSprings(vrm, dt, 4)` (`core/src/cloth.js`): `vrm.update` once (humanoid, lookAt, expressions),
   spring bones in 4 fixed substeps.
