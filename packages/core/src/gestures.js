// Co-speech gesture timing. A gesture phrase (pack clip with layer "gesture", see
// docs/contracts.md) has a preparation, one or more strokes and a retraction. The scheduler
// plays the preparation, holds at the pre-stroke pose (a speaker's pre-stroke hold) until
// Annie's voice has a strong syllable onset, then fires the stroke so it lands on that
// syllable's vowel; each further stroke of a multi-stroke phrase holds and fires the same way
// on the next stressed syllable.
// Everything is causal, so live and replay behave the same: onsets come from her audio level
// as it plays. Transcript deltas run ~0.3-0.9 s ahead of the audio: a sentence-final
// punctuation mark blocks new phrases until the next sentence starts (no stroke into the
// pause, none in the last moments before she stops), and emphatic words bring the next
// phrase forward.

const VOICED = -40; // dBFS
const RISE = 6; // dB over the last 160 ms makes an onset
const REFRACT = 0.12; // s between onsets
const QUIET = 0.3; // s of silence that ends an utterance
const STRENGTH_WAIT = 0.03; // s after an onset before its level says whether it is stressed
const STROKE_AT = 0.08; // s from a strong onset to the stroke: the syllable's vowel nucleus
const STRIKE_MAX = 1.5; // fastest stroke playback: its hit's deceleration grows with rate^2
const HOLD_ANY = 0.35; // s of holding after which any onset fires the stroke, not only a stressed one
const HOLD_MAX = 0.6; // s at the pre-stroke pose before the gesture is given up
const CREEP = 0.15; // playback rate while holding: a held pose still breathes
const END_BLOCK = 1.0; // s: no new phrase after a sentence end until the next sentence (or this long)
// Energy (Jev gentle/moderate/high): rest gap between phrases (s), amplitude, post-stroke speed.
const ENERGY = {
  gentle: { gap: [1.2, 2.4], amp: 0.8, speed: 0.92, bucket: "calm" },
  moderate: { gap: [0.6, 1.4], amp: 1.0, speed: 1.0, bucket: "animated" },
  high: { gap: [0.3, 0.8], amp: 1.12, speed: 1.08, bucket: "excited" },
};
const EMPHATIC = /\b(really|so|such|very|totally|super|love|amazing|awesome|happy|sorry|scary|favou?rite|never|always|all|every|best|yes|no)\b|!/i;
const SELF = /\b(i|i'm|i've|me|my|myself)\b/i;
const YOU = /\b(you|your|you're)\b/i;

export class OnsetDetector {
  constructor() {
    this.hist = [];
    this.last = -Infinity;
    this.peaks = []; // early peak levels of the current utterance's onsets
    this.pending = null; // an onset whose strength is still being measured
    this.silentSince = 0;
    this.voiced = false;
    this.strongAt = -Infinity; // time of the latest strong onset
  }

  // db: Annie's level now (null when silent). An onset is strong (a stressed syllable) when
  // its level ~1 frame in reaches the utterance's running median, or it opens an utterance.
  // (The rise itself says little: words start from near-silence, so every rise is large.)
  update(db, now) {
    const x = db ?? -120;
    const h = this.hist;
    h.push(now, x);
    while (h.length > 2 && now - h[0] > 0.16) h.splice(0, 2);
    let mn = Infinity;
    for (let i = 1; i < h.length; i += 2) mn = Math.min(mn, h[i]);
    const quiet = this.silentSince == null ? 0 : now - this.silentSince;
    this.voiced = x > VOICED;
    if (this.voiced) this.silentSince = null;
    else this.silentSince ??= now;
    if (quiet > QUIET) this.peaks.length = 0;
    const pd = this.pending;
    if (pd) {
      pd.peak = Math.max(pd.peak, x);
      if (now - pd.t >= STRENGTH_WAIT) {
        const pk = [...this.peaks].sort((a, b) => a - b);
        if (pd.first || pd.peak >= pk[pk.length >> 1]) this.strongAt = pd.t;
        this.peaks.push(pd.peak);
        this.pending = null;
      }
    }
    if (!this.voiced || x - mn <= RISE || now - this.last <= REFRACT) return 0;
    this.last = now;
    this.pending = { t: now, peak: x, first: !this.peaks.length || quiet > 0.15 };
    return x - mn;
  }
}

export class GestureScheduler {
  // phrases: ClipSamplers carrying their pack entry (hands, kind, energy, rest, prep_end, stroke, when)
  constructor(phrases, rand = Math.random) {
    this.phrases = phrases;
    this.rand = rand;
    this.onset = new OnsetDetector();
    this.cur = null; // {p, S: stroke times, k: next stroke, H: its hold point, t, rate, want, holdFrom, fired, onset, lastOnset}
    this.nextAt = 0;
    this.lastEnd = 0;
    this.lastHands = null;
    this.blockUntil = -Infinity; // sentence end seen in the transcript
    this.urgeAt = -Infinity;
    this.hint = null; // "self" | "you"
    this.text = "";
    this.strokes = []; // log: {t, name, k (stroke of the phrase), onset, held}
    this.stats = { started: 0, aborted: 0, timedOut: 0 };
  }

  hear(db, now) {
    const quiet = this.onset.silentSince == null ? 0 : now - this.onset.silentSince;
    const s = this.onset.update(db, now);
    // The next sentence has started once the voice comes back after a breath.
    if (s && quiet > 0.15) this.blockUntil = Math.min(this.blockUntil, now);
    return s;
  }

  // Cumulative assistant transcript; only the new part matters.
  transcript(text, now) {
    if (!text.startsWith(this.text.slice(0, 20))) this.text = "";
    const add = text.slice(this.text.length);
    this.text = text;
    if (!add.trim()) return;
    if (/[.!?…]["')\]]*\s*$/.test(add)) this.blockUntil = now + END_BLOCK; // cleared by the next sentence's first onset
    if (EMPHATIC.test(add)) this.urgeAt = now;
    this.hint = SELF.test(add) ? "self" : YOU.test(add) ? "you" : this.hint;
  }

  // Per frame while the talk layer is up. allowed: she is speaking and no semantic clip owns
  // the body; rest: the posture of the talk-rest loop underneath (phrases start and end in it).
  // Returns the phrase to show and its clip time, or null for the talk-rest loop.
  update(now, dt, { allowed, energy, rest }) {
    const E = ENERGY[energy] ?? ENERGY.moderate;
    const on = this.onset;
    const c = this.cur;
    if (c) {
      const p = c.p, S = c.S;
      if (c.k < S.length) {
        // Before the first stroke a pause or a semantic clip cancels the phrase; between the
        // strokes of a multi-stroke phrase, a pause while holding drops the remaining beats.
        const silent = on.silentSince != null && now - on.silentSince > QUIET * 0.8;
        const holding = c.holdFrom != null && c.fired == null;
        if (!allowed || (silent && (c.k === 0 || holding))) return this.stats.aborted++, this.#finish(now, E, c.k === 0);
        if (c.fired == null && c.t >= c.H - 0.08) {
          // At the pre-stroke pose: hold (easing in) until a stressed syllable later than the
          // one the previous stroke took; after a while any such syllable will do.
          c.holdFrom ??= now;
          c.want = CREEP;
          const held = now - c.holdFrom;
          const after = Math.max(c.holdFrom - 0.06, c.lastOnset + 1e-3);
          const at = on.strongAt >= after ? on.strongAt : held > HOLD_ANY && on.last >= Math.max(after, c.holdFrom + HOLD_ANY - 0.1) ? on.last : null;
          // No syllable to land on: give the gesture up (back to rest) rather than beat on nothing.
          if (at == null && held > HOLD_MAX) return this.stats.timedOut++, this.#finish(now, E, c.k === 0);
          if (at != null) {
            c.fired = now;
            c.onset = at;
            c.want = Math.min(STRIKE_MAX, Math.max(0.6, (S[c.k] - c.t) / Math.max(0.04, at + STROKE_AT - now)));
          }
        }
        c.rate += (c.want - c.rate) * (1 - Math.exp(-dt / (c.fired == null ? 0.05 : 0.06)));
        c.t += c.rate * dt;
        if (c.fired == null) c.t = Math.min(c.t, S[c.k] - 0.02); // a hold never creeps into its stroke
        if (c.t >= S[c.k]) {
          const at = now - (c.t - S[c.k]) / c.rate;
          this.strokes.push({ t: +at.toFixed(3), name: p.name, k: c.k, onset: +c.onset.toFixed(3), held: +(c.fired - (c.holdFrom ?? c.fired)).toFixed(2) });
          c.lastOnset = c.onset;
          c.k++;
          c.holdFrom = c.fired = c.onset = null;
          c.want = E.speed; // the rebound after the hit plays at the phrase's own speed
          // The next beat's pre-stroke pose: as far before its stroke as the first stroke's
          // preparation ended before it, but after the beat just struck.
          if (c.k < S.length) c.H = Math.max(S[c.k - 1] + 0.06, S[c.k] - c.L);
        }
      } else c.rate += (E.speed - c.rate) * (1 - Math.exp(-dt / 0.15)), (c.t += c.rate * dt); // glide out of the strike
      if (c.t >= p.duration) return this.#finish(now, E, false);
      return { phrase: p, t: c.t, amp: c.amp, rate: c.rate };
    }
    if (!allowed || !this.phrases.length || now < this.blockUntil || !on.voiced) return null;
    const urged = now - this.urgeAt < 0.6;
    if (now < this.nextAt - (urged ? 0.5 * (this.nextAt - this.lastEnd) : 0)) return null;
    const p = this.#pick(E.bucket, rest);
    if (!p) return null;
    const S = p.strokes?.length ? [...p.strokes].sort((a, b) => a - b) : [p.stroke];
    // Size is chosen once per phrase: re-reading energy every frame snapped the pose when Jev
    // changed it mid-phrase. Contact phrases (hand on chest, clasped hands) play as authored,
    // since scaling moves the contact point off the body or into it.
    const amp = p.contact ? 1 : E.amp;
    this.cur = { p, S, amp, k: 0, H: p.prep_end, L: S[0] - p.prep_end, t: 0, rate: 1, want: 1, holdFrom: null, fired: null, onset: null, lastOnset: -Infinity };
    this.stats.started++;
    this.lastHands = p.hands;
    return { phrase: p, t: 0, amp, rate: 1 };
  }

  get phrase() {
    return this.cur?.p ?? null;
  }

  // The talk layer went down (silence, a semantic clip): drop the phrase; the character
  // inertializes back to whatever the body shows next.
  cancel(now) {
    if (this.cur) this.#finish(now, ENERGY.moderate, this.cur.k === 0);
  }

  #finish(now, E, aborted) {
    this.cur = null;
    this.lastEnd = now;
    const [a, b] = E.gap;
    this.nextAt = now + (aborted ? 0.4 : a + (b - a) * this.rand());
    return null;
  }

  // Same rest posture as the loop; energy bucket favoured; alternate hands; transcript hints
  // favour matching deictics.
  #pick(bucket, rest) {
    const src = this.phrases.filter((p) => !rest || !p.rest || p.rest === rest);
    if (!src.length) return null;
    const w = src.map((p) => {
      let x = p.kind === "beat" ? 3 : p.kind === "metaphoric" ? 2 : 1;
      if (p.energy && p.energy !== bucket) x *= p.energy === "calm" || bucket === "calm" ? 0.15 : 0.4;
      if (this.lastHands && p.hands && p.hands !== "both" && p.hands === this.lastHands) x *= 0.25;
      if (p.kind === "deictic" && this.hint && new RegExp(this.hint === "self" ? "\\b(me|myself|herself|I)\\b" : "\\byou\\b", "i").test(p.when ?? "")) x *= 3;
      return x;
    });
    let r = this.rand() * w.reduce((s, x) => s + x, 0);
    for (let i = 0; i < src.length; i++) if ((r -= w[i]) <= 0) return src[i];
    return src.at(-1);
  }
}
