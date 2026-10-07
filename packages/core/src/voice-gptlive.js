import { LiveAnalyser, LiveMouth } from "./lipsync.js";

// GPT-Live-1 over browser WebRTC. The broker does the SDP exchange with the server
// key (GPT-Live has no ephemeral client keys); audio flows browser <-> OpenAI and
// never touches fal. Exchange and event names: broker/UPSTREAM.md §1.3-1.4.
// GPT-Live sends no turn-done, speech-started, or audio-done events, so finals,
// audio start/end, and barge-in are inferred here. Measured on a live session:
// transcript deltas arrive in bursts up to ~2.6 s apart mid-sentence, and the user's
// input transcript lags their voice by ~1.4 s, longer than GPT-Live takes to yield.
// So turn ends use audio energy *and* transcript, and barge-in uses a local mic VAD.

const TURN_QUIET_S = 1.5; // no Annie audio and no transcript this long ends her turn
const BARGEIN_HOLD_S = 0.35; // user voice sustained this long, starting over her speech
const MIC_GATE = 0.15; // mic aperture treated as voice (~ -37 dBFS)

export class GptLiveVoice {
  // `mic` may be any MediaStream (a synthetic one drives scripted live recordings);
  // `onRaw` sees every data-channel event, for logging.
  constructor({ bus, brokerUrl, voice = "marin", instructions, clock, mic, onRaw }) {
    Object.assign(this, { bus, brokerUrl: brokerUrl.replace(/\/$/, ""), voice, instructions, clock, mic, onRaw });
    this.user = { text: "", last: -Infinity, open: false };
    this.asst = { text: "", last: -Infinity, open: false };
    this.lastAnnieActive = -Infinity; // last time her audio or transcript showed activity
    this.userVoice = { since: null, quietSince: null, overAnnie: false, barged: false };
    this.usageSeconds = 0;
  }

  async connect() {
    this.pc = new RTCPeerConnection();
    this.mic ??= await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    for (const t of this.mic.getTracks()) this.pc.addTrack(t, this.mic);
    this.dc = this.pc.createDataChannel("oai-events"); // must exist before the offer
    this.dc.onmessage = (m) => {
      const e = JSON.parse(m.data);
      this.onRaw?.(e);
      this.#event(e);
    };
    this.audioCtx = new AudioContext();
    this.micAnalyser = new LiveAnalyser(this.audioCtx, this.audioCtx.createMediaStreamSource(this.mic));
    this.pc.ontrack = (e) => {
      // Audible playout stays on the element so the platform echo canceller sees it;
      // the analysers (energy + HeadAudio worklet) tap the same stream for the mouth.
      this.el = Object.assign(new Audio(), { autoplay: true, srcObject: e.streams[0] });
      this.remote = e.streams[0];
      this.analyser = new LiveMouth(this.audioCtx, this.audioCtx.createMediaStreamSource(e.streams[0]));
    };
    await this.pc.setLocalDescription(await this.pc.createOffer());
    await new Promise((res) => {
      if (this.pc.iceGatheringState === "complete") return res();
      this.pc.onicegatheringstatechange = () => this.pc.iceGatheringState === "complete" && res();
    });
    const r = await fetch(`${this.brokerUrl}/session`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ sdp: this.pc.localDescription.sdp, voice: this.voice, instructions: this.instructions }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error ?? `session ${r.status}`);
    const s = await r.json();
    this.sessionId = s.session_id;
    this.token = s.token;
    this.expiresAt = s.expires_at;
    await this.pc.setRemoteDescription({ type: "answer", sdp: s.sdp });
    return s;
  }

  #event(e) {
    const now = this.clock.now();
    switch (e.type) {
      case "session.input_transcript.delta": {
        const u = this.user;
        if (!u.open) (u.open = true), (u.text = "");
        u.text += e.delta;
        u.last = now;
        this.bus.emit("user.partial", { text: u.text.trim() });
        break;
      }
      case "session.output_transcript.delta": {
        const a = this.asst;
        if (!a.open) {
          a.open = true;
          a.text = "";
          this.bus.emit("assistant.audio.start", {});
        }
        a.text += e.delta;
        a.last = now;
        this.lastAnnieActive = now;
        this.bus.emit("assistant.partial", { text: a.text.trim() });
        break;
      }
      case "session.usage.updated":
        this.usageSeconds = e.usage?.seconds ?? this.usageSeconds;
        this.bus.emit("usage", { seconds: this.usageSeconds });
        break;
      case "session.closed":
        this.bus.emit("session.closed", { reason: e.reason });
        break;
      case "error":
        this.bus.emit("voice.error", { code: e.error?.code, message: e.error?.message });
        break;
    }
  }

  // Called every frame: returns mouth features and runs the turn/barge-in inference.
  tick(now, mouthSpeaking) {
    this.speaking = mouthSpeaking;
    if (mouthSpeaking) this.lastAnnieActive = now;
    const u = this.user, a = this.asst;
    if (u.open && now - u.last > TURN_QUIET_S) {
      u.open = false;
      this.bus.emit("user.final", { text: u.text.trim() });
    }
    if (a.open && now - this.lastAnnieActive > TURN_QUIET_S) {
      a.open = false;
      this.bus.emit("assistant.final", { text: a.text.trim() });
      this.bus.emit("assistant.audio.end", {});
    }
    // Local mic VAD: a sustained voice onset over her speech is a barge-in; a short
    // "mm-hmm" is not. Runs on the mic we send, so it beats the input transcript.
    const m = this.micAnalyser?.sample();
    this.micLevel = m?.amp ?? 0; // for the HUD waveform
    const v = this.userVoice;
    if (m && m.amp > MIC_GATE) {
      v.quietSince = null;
      if (v.since == null) {
        v.since = now;
        v.overAnnie = mouthSpeaking || now - this.lastAnnieActive < 0.2;
      }
      if (v.overAnnie && !v.barged && now - v.since >= BARGEIN_HOLD_S) {
        v.barged = true;
        this.bus.emit("bargein", {});
        // She yields on her own; what she says next is a new turn, not the cut one.
        if (this.asst.open) {
          this.asst.open = false;
          this.bus.emit("assistant.final", { text: this.asst.text.trim() });
          this.bus.emit("assistant.audio.end", {});
        }
      }
    } else if (v.since != null) {
      v.quietSince ??= now;
      if (now - v.quietSince > 0.6) (v.since = null), (v.barged = false); // one barge-in per utterance ("Wait, wait.")
    }
    this.userVoiced = v.since != null;
    return this.analyser?.sample() ?? null;
  }

  close() {
    try {
      this.dc?.readyState === "open" && this.dc.send(JSON.stringify({ type: "session.close" }));
    } catch {}
    setTimeout(() => {
      this.pc?.close();
      this.mic?.getTracks().forEach((t) => t.stop());
    }, 1500);
  }
}
