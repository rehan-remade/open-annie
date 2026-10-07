import { analyseClip, clipFeatureAt } from "./lipsync.js";

// Scripted voice: plays pre-rendered lines on a timeline and emits the same bus
// events GPT-Live would (transcript deltas, audio start/end, barge-in). Drives the
// keyless preview and the deterministic demo capture.

// GPT-Live transcript deltas run ahead of audible speech; simulate a small lead.
const TRANSCRIPT_LEAD = 0.12;

export function buildTimeline(lines, plan) {
  const byId = Object.fromEntries(lines.map((l) => [l.id, l]));
  let t = plan.start ?? 0;
  const beats = [];
  for (const step of plan.steps) {
    const line = byId[step.id];
    if (!line) throw new Error(`no line ${step.id}`);
    const at = step.at != null ? beats.find((b) => b.id === step.at.after).start + step.at.offset : t + (step.gap ?? 0.6);
    beats.push({ ...line, start: at, end: at + line.duration_s, bargein: step.bargein });
    t = Math.max(t, at + line.duration_s);
  }
  return { beats, end: t + (plan.tail ?? 3) };
}

export class ScriptedVoice {
  constructor({ bus, timeline, baseUrl }) {
    Object.assign(this, { bus, timeline, baseUrl });
    this.state = new Map(); // id -> {words emitted, started, ended}
    this.tracks = {};
    this.audio = {};
  }

  // Decode each Annie line once and pre-analyse it for the mouth.
  async load() {
    const ctx = new OfflineAudioContext(1, 24000, 24000);
    await Promise.all(
      this.timeline.beats.map(async (b) => {
        const buf = await fetch(`${this.baseUrl}/${b.file}`).then((r) => r.arrayBuffer());
        this.audio[b.id] = buf.slice(0);
        if (b.who === "annie") {
          const dec = await ctx.decodeAudioData(buf);
          this.tracks[b.id] = await analyseClip(dec.getChannelData(0), dec.sampleRate);
        }
      }),
    );
  }

  // Mouth features for whatever Annie line is audible at time t.
  mouthAt(t) {
    for (const b of this.timeline.beats) {
      if (b.who === "annie" && t >= b.start && t < b.end + 0.1) return clipFeatureAt(this.tracks[b.id], t - b.start);
    }
    return null;
  }

  activeBeats(t) {
    return this.timeline.beats.filter((b) => t >= b.start - TRANSCRIPT_LEAD && t < b.end + 0.3);
  }

  tick(t) {
    for (const b of this.timeline.beats) {
      const s = this.state.get(b.id) ?? { n: 0, started: false, ended: false, barged: false };
      this.state.set(b.id, s);
      const lead = b.who === "annie" ? TRANSCRIPT_LEAD : 0;
      const local = t - b.start + lead;
      if (local < 0 || s.ended) continue;
      if (!s.started) {
        s.started = true;
        if (b.who === "annie") this.bus.emit("assistant.audio.start", { id: b.id });
        this.bus.emit(b.who === "annie" ? "assistant.speaking" : "user.speaking", { id: b.id });
      }
      if (b.bargein && !s.barged && t - b.start >= b.bargein) {
        s.barged = true;
        this.bus.emit("bargein", { id: b.id });
      }
      // User transcripts arrive as the words finish; the assistant's as they start.
      const n = b.words.filter((w) => (b.who === "annie" ? w.start_s : w.end_s) <= local).length;
      const kind = b.who === "annie" ? "assistant" : "user";
      if (n > s.n) {
        s.n = n;
        const text = b.words.slice(0, n).map((w) => w.word).join(" ");
        if (n < b.words.length) this.bus.emit(`${kind}.partial`, { text, id: b.id });
      }
      if (t >= b.end + (b.who === "annie" ? 0 : 0.25)) {
        s.ended = true;
        this.bus.emit(`${kind}.final`, { text: b.words.map((w) => w.word).join(" "), id: b.id });
        if (b.who === "annie") this.bus.emit("assistant.audio.end", { id: b.id });
      }
    }
  }
}
