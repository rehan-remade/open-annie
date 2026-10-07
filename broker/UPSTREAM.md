# Upstream API facts (verified 2026-09-27)

Everything below was read from the live docs on **2026-09-27** (Markdown versions,
fetched by appending `.md` to each docs URL) or measured from this machine that day.
Items marked **UNVERIFIED** are inferences the docs do not state outright. The
browser client should be built from this file.

---

## 1. OpenAI GPT-Live (`gpt-live-1`)

Sources:
- https://developers.openai.com/api/docs/guides/voice-webrtc (GPT-Live tab: "Connect a browser to GPT-Live")
- https://developers.openai.com/api/docs/guides/live-conversations
- https://developers.openai.com/api/docs/models/gpt-live-1
- https://developers.openai.com/api/docs/guides/voice-server-controls
- https://developers.openai.com/api/reference/resources/live/sideband-websocket (full event schemas)
- https://developers.openai.com/api/reference/resources/live/primary-websocket (full `SessionConfig` schema)
- https://developers.openai.com/api/docs/guides/voice-latency-cost (billing)
- https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create

### 1.1 Model facts

- Model id and only snapshot: `gpt-live-1`. Full-duplex (it listens and speaks at the same time).
  Audio and text in and out, no image or video. Knowledge cutoff Jul 31 2025.
- Price: **$0.05 per minute, billed per second**, not rounded up. Active time counts
  everything from start to close, silence included. Backend (delegated Responses) usage is billed separately.
- Endpoints: only `v1/live/sessions`. The model page lists `v1/realtime` as **not supported**.
- Rate limits are concurrent sessions: Tier 1 25, Tier 2 50, Tier 3 200, Tier 4 300, Tier 5 500. Free tier is not supported.
- Context: 128,000 tokens. Above 90% usage GPT-Live swaps in a replacement engine inside the same session.
- Data: ZDR-eligible. `store` defaults to false. There is no transcript retention unless `store: true`.

### 1.2 No ephemeral client keys for GPT-Live

- The GPT-Live WebRTC guide only describes the server-side SDP exchange with the project key:
  "Keep the key and session configuration on your trusted server."
- `POST /v1/realtime/client_secrets` exists for the **Realtime API**. Its `session.model` enum lists
  `gpt-realtime*` / `gpt-4o*-realtime*` / `gpt-audio*` models, and **`gpt-live-1` is not among them**.
  The gpt-live-1 model page also marks `v1/realtime` unsupported.
  Conclusion: the browser cannot get a GPT-Live ephemeral key. The broker has to do the exchange.

### 1.3 Session creation (the SDP exchange the broker does)

```
POST https://api.openai.com/v1/live/sessions
Authorization: Bearer $OPENAI_API_KEY
Content-Type: application/json

{
  "session": {
    "model": "gpt-live-1",                       // required
    "instructions": "...",                       // optional, <= 16,384 tokens, immutable after startup
    "audio": { "output": { "voice": "marin" } }, // optional; default voice "marin"; omit audio.format for WebRTC
    "input": [ ... ],                            // optional text history, <= 128 msgs / 8,192 tokens
    "delegation": null | {"type":"client"} | {"type":"responses","responses":{...}},  // omitted/null = client
    "store": false,                              // optional, default false
    "client": {                                  // optional: permissions for the untrusted WebRTC frontend
      "data_channel": {
        "allowed_client_events": "all" | ["session.close", ...],   // omitted = allow all
        "allowed_server_events": "all" | [{"type": "..."}, ...]      // omitted = allow all
      }
    }
  },
  "transport": { "type": "webrtc", "sdp": "<browser SDP offer>" }
}
```

- It is **JSON**, not multipart and not `application/sdp`. The multipart and `application/sdp` bodies belong to the older
  Realtime `/v1/realtime/calls` flow shown lower on the same page.
- Success is **HTTP 201** with JSON:
  ```json
  { "session": { "id": "live_123" }, "transport": { "type": "webrtc", "sdp": "<SDP answer>" } }
  ```
  The session id is `session.id` in the body. The docs describe no `Location` header; that header also belongs to the
  Realtime `/v1/realtime/calls` flow. Treat the id as opaque and keep its prefix.
  The broker also accepts 200, because the SIP variant of the same endpoint documents `200 OK`.
- **`expires_at` is not in the documented create response.** It appears in the `session` object of `session.started`,
  `session.updated` and `session.closed` as a Unix timestamp in seconds. The broker's `/session` returns its own `expires_at`,
  which is the broker cap deadline `now + ANNIE_SESSION_CAP_S`, lowered to upstream's value if upstream ever includes one.
- **The HTTP request starts the session.** Do not send `session.start` on the data channel.
- **Billing: creating a WebRTC session pre-bills 15 s of voice duration.** That amount is credited against the running
  duration, not added on top. In practice every session costs at least 15 s.
- Voices (enum in `SessionConfig`): alloy ash ballad beacon bossa cedar cinder coral delta echo gleam marin meridian quartz
  ripple sage shimmer stone tempo verse vesper willow, plus custom `{id}`. Voice cannot change after startup.
- The official SDKs expose this as `client.live.create(session=..., transport={"type":"webrtc","sdp":...})`.
  The broker calls the HTTP endpoint directly with httpx.
- Optional header `OpenAI-Safety-Identifier` (a hashed end-user id) is documented for Realtime requests.
  **UNVERIFIED** for `/v1/live/sessions`, so the broker sends it only when `ANNIE_SAFETY_IDENTIFIER=1`.
- Related endpoints: `POST /v1/live/sessions/{id}/fork` (WebRTC fork) and `GET /v1/live/sessions/{id}/content`
  (stored recording). `POST /v1/live/sessions/{id}/hangup` takes no body and returns 200 with an empty body; it is
  documented in the SIP guide. The close-reason table says `close_requested` covers "sent `session.close` or called the
  hangup endpoint". **UNVERIFIED** that hangup works for WebRTC sessions; the broker only uses it as a best-effort fallback.

### 1.4 Browser side (what `packages/core` must do)

This is the connection sequence from the docs, in order:
1. Call `getUserMedia({audio:true})` from a user gesture and `addTrack` the mic to an `RTCPeerConnection`.
2. **Create the data channel before the offer:** `pc.createDataChannel("oai-events")`. The label is `oai-events`.
3. `createOffer()` → `setLocalDescription()` → **wait until `iceGatheringState === "complete"`**. The docs example allows up
   to 10 s; there is no trickle ICE to OpenAI. Then send `pc.localDescription.sdp`.
4. `POST {broker}/session` with `{sdp, voice?, instructions?}`, then apply
   `setRemoteDescription({type:"answer", sdp: result.sdp})`. Our broker flattens OpenAI's
   `transport.sdp` to `sdp` and `session.id` to `session_id`.
5. Remote audio arrives as a media track (`pc.ontrack`). **Audio never travels on the data channel over WebRTC.** Do not
   send `session.input_audio.append`, and expect no `session.output_audio.delta`.
6. Wait for `session.started` before sending commands.
7. To end: register a `session.closed` handler, send `{"type":"session.close"}`, and keep the peer connection open
   until `session.closed` arrives. The docs example times out after 15 s. Then stop the tracks and close.

Server → browser events on `oai-events`. Every event has `type` and `event_id`, and it may carry `client_event_id`
when it answers a command you sent with `event_id`:

| type | payload (beyond type/event_id) |
|---|---|
| `session.started` | `session: {id, expires_at (unix s), model, status:"active", instructions, input, audio:{output:{voice}}, delegation}` |
| `session.input_transcript.delta` | `delta: string, start_ms: number, end_ms: number` (the user's speech) |
| `session.output_transcript.delta` | `delta: string, start_ms: number, end_ms: number` (the assistant's speech) |
| `session.usage.updated` | `usage: {seconds}` is **cumulative**, so don't sum it; `context_window?: {usage_ratio}` |
| `session.closed` | `reason: "close_requested"\|"expired"\|"content"\|"remote_hangup"\|"connection_lost"`, `session: {...}`, `usage: {seconds}` |
| `session.updated` | `session: {...}` (ack of `session.update`) |
| `session.input_audio.muted` / `.unmuted` | ack of `session.input_audio.mute` / `.unmute` |
| `session.instructions.appended` / `session.thinking.appended` / `session.commentary.appended` | ack of the matching `.append`; the ack carries `start_ms`/`end_ms` |
| `session.delegation.created` | `delegation: {id, target, type, response_id}, offset_ms` |
| `response.event` | nested Responses event (only with Responses delegation) |
| `error` | `error: {type, code, message, param?, client_event_id?}` |
| `info` | `code, message` (e.g. `code: "data_channel_permissions"`) |

Example transcript delta (verbatim from the reference):
```json
{"type":"session.input_transcript.delta","event_id":"evt_input_transcript_001","delta":"A table for two at seven, please.","start_ms":1600,"end_ms":3400}
```

Transcript semantics (from the docs):
- Append each speaker's `delta` fragments **exactly as received**, spaces included.
- `start_ms`/`end_ms` are ms from session start and approximate.
- **There is no item id, no "done" event, and no end-of-turn marker.** The app decides how to group fragments.
- Both speakers' captions can grow at the same time because the model is full duplex.
- **There are no speech-started or speech-stopped events and no output-audio-done event.** So the core bus events
  `assistant.audio.start/end` and `bargein` have to come from local analysis of the remote track and the mic, such as RMS or VAD.
  `assistant.final` / `user.final` have to come from a silence gap or timing heuristic.

Client → server commands the browser may send (unless restricted with `client.data_channel.allowed_client_events`):
`session.close`, `session.input_audio.mute`, `session.input_audio.unmute`, and `session.instructions.append` /
`session.thinking.append` / `session.commentary.append` (`{content: string <= 500 tokens, delegation_id: null, event_id?}`).
With Responses delegation it may also send `session.update`, `response.item.create` and `response.create`.
Never send `session.start` over WebRTC.

With client delegation (the broker's default, since it omits `delegation`), GPT-Live can still emit `session.delegation.created`
and wait for results. Annie has no backend agent, so the persona instructions should tell it not to delegate.

### 1.5 Sideband (server attach), used by `reaper.py`

- `wss://api.openai.com/v1/live/sessions/{session_id}/attach` with `Authorization: Bearer $OPENAI_API_KEY` from the same
  project. The docs require no other header. Attaching does not replay earlier events and does not emit anything by itself.
- The server receives the conversation events: transcript deltas, `session.usage.updated`, `session.closed`, delegation and `error`.
  It **also receives reflected audio**: `session.input_audio.append {audio}` and
  `session.output_audio.delta {delta, start_ms, end_ms}`, both base64 mono PCM16LE at 24 kHz with no `event_id`.
  That is roughly 64 KB/s of base64 per direction per session. The reaper skips parsing those.
- The server can send `session.close` (then receive `session.closed`), plus `session.update`, the three appends, and mute/unmute.
- Do not send `session.start` or `session.input_audio.append` on the sideband.
- **UNVERIFIED live:** the whole sideband contract is only documented, not exercised, because there was no OpenAI key.
  The reaper can be switched off with `ANNIE_SIDEBAND=0`. It falls back to wall-clock reaping on any attach error:
  close at the cap, best-effort hangup, bill the elapsed wall-clock time.
- Caveat: the docs say transcript gaps alone do not prove silence. The reaper's 30 s idle rule counts transcript deltas
  and assistant output audio as activity, and nothing else.

---

## 2. TypeSafe Jev

Sources: https://docs.typesafe.ai/api (`/api.md`), https://docs.typesafe.ai/models (`/models.md`),
https://docs.typesafe.ai/llms-full.txt, https://developers.cloudflare.com/ai/models/typesafe/jev/

### 2.1 Request

```
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json

{"model": "jev-latest", "state": <string | object | array>, "questions": {"<id>": Question, ...}}
```

Question types. All three take `type` and `instructions`, where `instructions` can be a string, object or array:
- `noul`: yes/no. `criteria` is optional: `{"true": "...", "false": "..."}`.
- `choice`: `criteria` is required and maps option → description or `null`, max 255 options.
- `score`: `criteria` is required and is an ordered array of level descriptions, 2 to 10 levels.

Question keys are never sent to the model.

### 2.2 Response (verified live 2026-09-27, see 2.5)

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "face": {"type": "choice", "choice": "neutral", "confidence": 0.49,
             "probabilities": {"concerned": 0.0, "surprised": 0.0, "happy": 0.2, "joyful": 0.2, "neutral": 0.6}},
    "wants_dance": {"type": "noul", "noul": 0.81}
  },
  "usage": {"input_tokens": 404, "output_tokens": 77}
}
```

- **A noul answer is `{type:"noul", noul: <0..1>}`.** It has no `confidence` and no `probabilities`, which corrects our earlier belief.
- A choice answer is `{type, choice, probabilities: {option: p}, confidence}`.
- A score answer is `{type, score, legend: {"0": desc, ...}, probabilities: {"0": p, ...}, confidence}`.
- `model` in the response is the resolved version id (`jev-latest` → `jev-1.13.0` today).
- The broker passes this through unchanged and adds `latency_ms`.

### 2.3 Limits, errors and pricing

- Price: **$42 per billion / $0.042 per million input tokens; output tokens are free.**
- Rate limits: **250,000 tokens/s and 1,200 requests/min.** The docs warn they "are adjusting dynamically".
  The broker's governor defaults to 1,000 rpm (`ANNIE_JEV_RPM`) with a burst of 20.
- Context: 64k tokens per request, of which 32k is for `state` plus the longest question. Text only.
- Errors: `401` bad or missing key, `422` validation, `429` rate limit, `529` overloaded. The docs recommend
  exponential backoff; the broker never retries and answers 503 so the browser uses its floor.
  Observed live: **a missing key returns `403`** with `{"detail":{"error_type":"authentication_error",...}}`,
  so the broker treats both 401 and 403 as "key rejected → disable".
- `GET /v1/models` (auth required, no token cost) lists the aliases. The broker uses it as the connection warm-up.
- The response headers include `x-typesafe-request-id`.

### 2.4 Browser CORS: verified rejected

A preflight `OPTIONS /v1/systemone` with `Origin: https://example.com` returned **HTTP/2 400** with **no
`access-control-allow-origin` header**. It did list allow-methods and allow-headers, but there was no origin, so browsers block it.
The JS SDK requires `dangerouslyAllowBrowser: true` to run in a browser, and the key would then be exposed. That confirms
the need for the proxy. The API serves **HTTP/2** (via Cloudflare).

### 2.5 Measured from this machine (2026-09-27, local broker → api.typesafe.ai, warm HTTP/2 pool)

Setup: one request in `/decide`, `state = {"user_said": "Let's dance! Turn the music up!", "annie_mood": "neutral"}`,
one 5-option `choice` (face) plus one `noul` (wants_dance), 404 input tokens (about $0.000017 per call).

| metric (n=20 sequential, after warm-up) | p50 | p90 | min | max | mean |
|---|---:|---:|---:|---:|---:|
| upstream `latency_ms` (broker → Jev → broker) | 108.0 ms | 149.4 ms | 83.2 | 204.7 | 116.8 |
| client round trip through the broker | 109.2 ms | 151.0 ms | 84.4 | 206.0 | 118.2 |

All 20 requests returned 200 and the first (cold) call took 86.9 ms after the warm-up `GET /v1/models`. The
browser's 700/900 ms budgets and the broker's 1.5 s deadline both leave headroom. The broker adds about 1 ms.
Observation: for "Let's dance!" Jev chose `neutral` (0.6) for the face while `wants_dance` came back 0.81. The face
question wording needs tuning in the core. That is a question-design issue, not an API issue.

### 2.6 Cloudflare Workers AI route (`typesafe/jev`)

The request is the same Jev body **minus `model`**:
- `env.AI.run('typesafe/jev', {state, questions})` in a Worker, or
- `POST https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/ai/run` with `Authorization: Bearer $CLOUDFLARE_API_TOKEN`
  and body `{"model": "typesafe/jev", "input": {state, questions}}`.

The documented response has the same `{model, answers, usage}` shape. Cloudflare lists a 32,000-token context window,
the same $0.042/M input and $0 output pricing, and zero data retention. **UNVERIFIED:** whether the REST `/ai/run` reply
wraps this in Cloudflare's `{result, success, errors}` envelope. The broker does not use this route.

---

## 3. fal serverless (fal 1.79.1)

Sources: https://fal.ai/docs/llms.txt; docs pages `documentation/development/realtime`,
`documentation/deployment/machine-types`, `documentation/serverless/pricing`,
`documentation/development/manage-secrets-securely`, `documentation/development/request-headers`,
https://fal.ai/docs/llms-full.txt (App reference, auth modes, `fal deploy` flags). I also read the source of the
`fal==1.79.1` wheel from PyPI: `fal/app.py` and `fal/api/api.py`.

- `@fal.endpoint(path, *, is_websocket=False, health_check=None)`. HTTP endpoints are registered as **POST only**
  (`_app.add_api_route(..., methods=["POST"])`). With `is_websocket=True` the method gets the raw FastAPI WebSocket and
  **must be `async def f(self, websocket: WebSocket)`**; this is validated at build time. `@fal.realtime()` is the separate
  msgpack protocol, which we don't use.
- POST and WS endpoints can share a path, since `RouteSignature` differs by `is_websocket`. This was verified by building
  `AnnieBroker` locally with fal 1.79.1: `/decide` shows up as both an `APIRoute [POST]` and an `APIWebSocketRoute`.
- There is no public API for GET routes. `App._add_extra_routes(app)` is the hook where fal itself adds `GET /health`,
  which calls `self.health()`. `fal_app.py` overrides that hook to add `GET /health` and `GET /status`.
  A `POST /status` twin exists in case the gateway only forwards POST (**UNVERIFIED** whether `fal.run` forwards GET).
- fal's `_build_app` **always adds `CORSMiddleware(allow_origins="*", allow_credentials=True)`**. The origin allowlist
  is therefore enforced inside the handlers, which return 403 `origin_not_allowed`. Verified locally: an evil-origin
  preflight gets 200 from fal's CORS, and the POST then gets 403 from the broker.
- `setup()` and `teardown()` may be `async`. fal calls them inside the FastAPI lifespan.
- App attributes: `machine_type` (CPU: `XS` 0.5 vCPU/512 MB, `S` 1 vCPU/1 GB (the default), `M` 2/2 GB, `L` 4/15 GB,
  `XL` 8/30 GB), `min_concurrency`, `max_concurrency`, `max_multiplexing` (concurrent requests per runner),
  `request_timeout` (seconds per request), `startup_timeout`, `app_auth` (`private` needs a fal key, `public` needs none,
  `shared` means callers pay), `requirements`, `local_python_modules`, `secrets` (an allowlist of secret names injected
  as env vars). `keep_alive` is passed as a class keyword (`class X(fal.App, keep_alive=300)`).
- Secrets: `fal secrets set NAME=value [NAME2=value2] [--env ENV]` are injected as env vars at runner start and read with `os.getenv`.
- URLs: `https://fal.run/<owner>/<app-name>/<path>`. Raw WebSocket: `wss://fal.run/<owner>/<app-name>/<path>`
  (from `fal_client._build_runner_ws_url`; private apps add `?fal_jwt_token=`).
- Gateway headers: `x-fal-request-id`, `x-fal-caller-user-id`, `x-fal-endpoint`, and custom caller headers are forwarded.
  **UNVERIFIED:** whether and how the gateway sets `X-Forwarded-For`. The broker reads the leftmost entry when
  `ANNIE_IP_HEADER=x-forwarded-for`, which is the fal default. Log it once after deploying to confirm.
- Scaling params persist across deploys unless you pass `fal deploy --reset-scale`.
- Pricing: serverless bills per second of runner life (SETUP, IDLE including keep_alive, RUNNING, DRAINING, TERMINATING).
  **CPU machine prices are not published** on fal.ai/pricing (GPU only, e.g. H100 $1.89/h); marked unmeasured.
- The facts above were checked against the fal 1.79.1 wheel; `broker/fal_app.py` has been deployed and run with it.
