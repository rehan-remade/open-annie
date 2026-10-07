import { analyseClip, clipFeatureAt } from "./lipsync.js";

// Replays a recorded live session (real GPT-Live audio + the bus events it produced)
// on the capture clock, through the same interface as ScriptedVoice.
// Session JSON: {duration, audio: {file, start}, bus: [{t, type, detail}], userPlays: [{id, t, file, duration_s, words}]}

const EMITTED = new Set(["user.partial", "user.final", "assistant.partial", "assistant.final", "assistant.audio.start", "assistant.audio.end", "bargein"]);

export class ReplayVoice {
  // `setTime` lets the stage clock read each event's exact recorded time while it is
  // emitted, so the pacer makes the same asks it made live, not frame-quantized ones.
  constructor({ bus, session, baseUrl, setTime }) {
    Object.assign(this, { bus, session, baseUrl, setTime });
    this.events = session.bus.filter((e) => EMITTED.has(e.type)).sort((a, b) => a.t - b.t);
    this.i = 0;
    this.timeline = this.#beats();
  }

  // Captions and camera need "beats": user lines as played, Annie turns as heard.
  #beats() {
    const beats = this.session.userPlays.map((u) => ({ ...u, who: "user", start: u.t, end: u.t + u.duration_s }));
    let cur = null;
    for (const e of this.events) {
      if (e.type === "assistant.audio.start") {
        cur = { id: `annie_${beats.length}`, who: "annie", start: e.t, end: Infinity, words: [], live: true };
        beats.push(cur);
      } else if (cur && (e.type === "assistant.partial" || e.type === "assistant.final")) {
        const words = e.detail.text.split(/\s+/).filter(Boolean);
        for (let k = cur.words.length; k < words.length; k++) cur.words.push({ word: words[k], start_s: e.t - cur.start });
        if (e.type === "assistant.final") cur.end = e.t;
      } else if (cur && (e.type === "assistant.audio.end" || e.type === "bargein")) {
        cur.end = Math.min(cur.end, e.t + (e.type === "bargein" ? 0.6 : 0));
      }
    }
    for (const b of beats) if (b.end === Infinity) b.end = this.session.duration;
    beats.sort((a, b) => a.start - b.start);
    return { beats, end: this.session.duration };
  }

  async load() {
    const buf = await fetch(new URL(this.session.audio.file, this.baseUrl)).then((r) => r.arrayBuffer());
    const ctx = new OfflineAudioContext(1, 48000, 48000);
    const dec = await ctx.decodeAudioData(buf);
    this.track = await analyseClip(dec.getChannelData(0), dec.sampleRate);
    this.audio = {};
  }

  mouthAt(t) {
    return clipFeatureAt(this.track, t - this.session.audio.start);
  }

  activeBeats(t) {
    return this.timeline.beats.filter((b) => t >= b.start && t < b.end + 0.3);
  }

  tick(t) {
    while (this.i < this.events.length && this.events[this.i].t <= t) {
      const e = this.events[this.i++];
      this.setTime?.(e.t);
      this.bus.emit(e.type, e.detail);
    }
    this.setTime?.(t);
  }
}
