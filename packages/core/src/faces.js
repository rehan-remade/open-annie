import { clamp, easeInOutCubic, lerp, mulberry32 } from "./bus.js";

// The face vocabulary Jev chooses from. Each entry maps to VRM 1.0 emotion presets
// (three-vrm maps VRM 0.x joy/sorrow/fun onto these names). `when` becomes Jev criteria.
export const FACES = {
  neutral: { w: {}, when: "no particular feeling; calm attention", family: "calm" },
  happy: { w: { happy: 0.7 }, when: "warm pleasure, friendly greeting, liking what was said", family: "warm" },
  joyful: { w: { happy: 1.0 }, when: "big delight, celebration, great news", family: "warm" },
  amused: { w: { happy: 0.5, relaxed: 0.3 }, when: "finding something funny or cheeky; laughing at a joke", family: "warm" },
  relieved: { w: { relaxed: 0.65, happy: 0.3 }, when: "a worry just went away; things turned out fine", family: "warm" },
  proud: { w: { happy: 0.55, relaxed: 0.3 }, when: "pleased with an achievement, confident self-presentation", family: "warm" },
  tender: { w: { relaxed: 0.55, happy: 0.3 }, when: "affection, gentle care, comforting someone", family: "warm" },
  playful: { w: { happy: 0.6, relaxed: 0.2 }, when: "teasing, mischief, light banter, joking refusal", family: "warm" },
  sad: { w: { sad: 1.0 }, when: "own sadness, loss, disappointment", family: "down" },
  concerned: { w: { sad: 0.75, surprised: 0.15 }, when: "worry for the other person; hearing about their trouble or illness", family: "down" },
  hurt: { w: { sad: 0.7, angry: 0.15 }, when: "feelings hurt by something said to her", family: "down" },
  angry: { w: { angry: 0.8 }, when: "real anger at an insult or injustice", family: "hot" },
  annoyed: { w: { angry: 0.4, relaxed: 0.1 }, when: "mild irritation, exasperation, sulking", family: "hot" },
  skeptical: { w: { angry: 0.2, relaxed: 0.25 }, when: "doubt, suspicion that a claim is wrong", family: "hot" },
  surprised: { w: { surprised: 0.85 }, when: "sudden unexpected news or being interrupted", family: "alert" },
  shocked: { w: { surprised: 1.0, sad: 0.1 }, when: "stunned by alarming news", family: "alert" },
  afraid: { w: { surprised: 0.5, sad: 0.4 }, when: "fear, feeling threatened or scared", family: "alert" },
  curious: { w: { surprised: 0.25, happy: 0.15 }, when: "interest in a question, wanting to know more", family: "alert" },
  thinking: { w: { relaxed: 0.2 }, when: "working something out, explaining how something works", family: "calm" },
  embarrassed: { w: { happy: 0.35, sad: 0.2 }, when: "bashful, flattered, caught out", family: "calm" },
  sleepy: { w: { relaxed: 0.8 }, when: "tired, drowsy, winding down", family: "calm" },
  bored: { w: { relaxed: 0.4, sad: 0.15 }, when: "uninterested, waiting", family: "calm" },
};

export const EMOTION_PRESETS = ["happy", "angry", "sad", "relaxed", "surprised"];

// Per-family onset and release, from the VTubing constants in the review.
const TIMING = {
  alert: { onset: 0.12, release: 0.3 },
  warm: { onset: 0.3, release: 0.42 },
  down: { onset: 0.45, release: 0.6 },
  hot: { onset: 0.25, release: 0.42 },
  calm: { onset: 0.25, release: 0.42 },
};

const APEX_CAP = 0.85; // never drive an emotion preset to 1.0
const DWELL = 1.1; // minimum seconds before a different face may replace the current one
const HOLD = 4.5; // a face with no renewal releases after this long

// FaceDirector turns discrete face picks into eased preset weights: onset, apex,
// sustain, release, dwell. Renewals extend the hold instead of restarting.
export class FaceDirector {
  constructor({ seed = 7 } = {}) {
    this.rand = mulberry32(seed);
    this.displayed = Object.fromEntries(EMOTION_PRESETS.map((k) => [k, 0]));
    this.from = { ...this.displayed };
    this.cur = null; // {name, t0, apexEnd, sustain, gain, intensity, holdUntil, timing}
    this.pending = null;
    this.lastChange = -Infinity;
    this.speechScale = 1;
    this.speaking = false;
  }

  set(name, intensity = 1, now) {
    if (!FACES[name]) return false;
    if (this.cur && this.cur.name === name) {
      this.cur.holdUntil = Math.min(now + HOLD, this.cur.t0 + 3 * HOLD);
      this.cur.intensity = Math.max(this.cur.intensity, intensity);
      return true;
    }
    const urgent = FACES[name].family === "alert";
    if (!urgent && now - this.lastChange < DWELL) {
      this.pending = { name, intensity };
      return false;
    }
    this.#start(name, intensity, now);
    return true;
  }

  #start(name, intensity, now) {
    this.from = { ...this.displayed };
    const timing = TIMING[FACES[name].family];
    this.cur = {
      name,
      t0: now,
      timing,
      intensity,
      apexEnd: now + timing.onset + 0.6 + 0.6 * this.rand(),
      sustain: 0.55 + 0.3 * this.rand(),
      gain: 0.88 + 0.24 * this.rand(),
      holdUntil: now + HOLD,
    };
    this.lastChange = now;
    this.pending = null;
  }

  get current() {
    return this.cur?.name ?? "neutral";
  }

  update(now, dt) {
    if (this.pending && now - this.lastChange >= DWELL) this.#start(this.pending.name, this.pending.intensity, now);
    const k = 1 - Math.exp(-dt / 0.3);
    this.speechScale = lerp(this.speechScale, this.speaking ? 0.85 : 1, k);

    const c = this.cur;
    const target = Object.fromEntries(EMOTION_PRESETS.map((p) => [p, 0]));
    let blendFrom = 0;
    if (c) {
      const t = now - c.t0;
      let env;
      if (t < c.timing.onset) {
        env = 1;
        blendFrom = 1 - easeInOutCubic(t / c.timing.onset);
      } else if (now < c.apexEnd) env = 1;
      else if (now < c.holdUntil) env = lerp(1, c.sustain, easeInOutCubic(clamp((now - c.apexEnd) / 0.5)));
      else env = c.sustain * (1 - easeInOutCubic(clamp((now - c.holdUntil) / c.timing.release)));
      if (now > c.holdUntil + c.timing.release) this.cur = null;
      const w = FACES[c.name].w;
      for (const p in w) target[p] = clamp(w[p] * env * c.gain * c.intensity * APEX_CAP);
    }
    for (const p of EMOTION_PRESETS) {
      this.displayed[p] = lerp(target[p], this.from[p], blendFrom) * this.speechScale;
    }
    return this.displayed;
  }
}
