import { clamp } from "./bus.js";

// Audio-driven mouth. GPT-Live emits no visemes and its transcript timing is
// approximate, so the mouth follows the audio the user actually hears.
// Two providers feed one feature frame {amp, db, vowels, visemes?}:
// - "energy": loudness sets aperture, a coarse formant-band guess picks the vowel.
//   Always on; it is the fallback when HeadAudio fails to load.
// - "headaudio" (vendor/headaudio, MIT): 15 Oculus visemes per 16 ms frame.
// mouth.js turns either into avatar weights.

export const VOWELS = ["aa", "ih", "ou", "ee", "oh"];
// Offline/capture only: the mouth reads the audio this far ahead of the clock
// (lipsync-eval/ measures the resulting A/V offset; target: mouth leads by 0-40 ms).
export const MOUTH_LEAD = 0.04;
// ?lipsync=energy forces the fallback provider (A/B checks).
const FORCE_ENERGY = typeof location !== "undefined" && new URLSearchParams(location.search).get("lipsync") === "energy";
const BANDS = [[250, 700], [700, 1200], [1200, 2200], [2200, 3600], [3600, 8000]];

function bands(mag, binHz) {
  const e = BANDS.map(([lo, hi]) => {
    let s = 0;
    for (let i = Math.ceil(lo / binHz); i <= Math.min(mag.length - 1, Math.floor(hi / binHz)); i++) s += mag[i] * mag[i];
    return s;
  });
  const tot = e.reduce((a, b) => a + b, 0) || 1;
  return e.map((v) => v / tot);
}

// Band ratios → vowel scores. Crude on purpose; it only has to beat a flapping jaw.
function vowelScores(r) {
  const [b1, b2, b3, b4, b5] = r;
  const s = {
    aa: 2.2 * b2 + 0.6 * b3 - 0.4 * b1,
    oh: 1.3 * b1 + 0.9 * b2 - 1.0 * b4,
    ou: 1.9 * b1 - 0.7 * b2 - 0.5 * b3,
    ee: 2.2 * b4 + 0.4 * b3 - 0.8 * b2,
    ih: 1.4 * b3 + 0.6 * b4 + 0.3 * b5 - 0.3 * b1,
  };
  const m = Math.max(...Object.values(s));
  let z = 0;
  for (const k in s) z += s[k] = Math.exp((s[k] - m) * 9);
  for (const k in s) s[k] /= z;
  return s;
}

// dBFS → aperture, the VTubing loudness gate: opens at -40, fully open at -20.
const aperture = (db) => clamp((db + 40) / 20);

export function featuresFrom(timeDomain, mag, binHz) {
  let sq = 0;
  for (let i = 0; i < timeDomain.length; i++) sq += timeDomain[i] * timeDomain[i];
  const db = 10 * Math.log10(sq / timeDomain.length + 1e-12);
  return { amp: aperture(db), db, vowels: vowelScores(bands(mag, binHz)) };
}

// Winner-take-most, asymmetric smoothing, silence gate, syllable-band limit.
export class MouthShaper {
  constructor({ gains = {}, floor = 0.3 } = {}) {
    this.w = Object.fromEntries(VOWELS.map((v) => [v, 0]));
    this.gains = { aa: 1, ih: 0.8, ou: 0.85, ee: 0.8, oh: 0.9, ...gains };
    this.floor = floor;
    this.env = 0;
    this.lastVoiced = -Infinity;
    this.speaking = false;
  }
  update(f, now, dt) {
    const voiced = f && f.amp > 0.04;
    if (voiced) this.lastVoiced = now;
    this.speaking = now - this.lastVoiced < 0.16;
    // Envelope low-passed toward the 2 to 7 Hz syllable band: attack ~30 ms, release ~100 ms.
    const a = voiced ? Math.max(this.floor, f.amp) : 0;
    this.env += (a - this.env) * (1 - Math.exp(-dt / (a > this.env ? 0.03 : 0.1)));
    const target = Object.fromEntries(VOWELS.map((v) => [v, 0]));
    if (voiced) {
      const ranked = VOWELS.map((v) => [v, f.vowels[v]]).sort((x, y) => y[1] - x[1]);
      target[ranked[0][0]] = 0.95;
      target[ranked[1][0]] = 0.4 * (ranked[1][1] / (ranked[0][1] || 1));
    }
    let sum = 0;
    for (const v of VOWELS) {
      const t = target[v] * this.env * this.gains[v];
      const k = t > this.w[v] ? 1 - Math.exp(-50 * dt) : 1 - Math.exp(-30 * dt);
      const nv = this.w[v] + (t - this.w[v]) * k;
      if (Math.abs(nv - this.w[v]) > 0.01 || t === 0) this.w[v] = nv;
      sum += this.w[v];
    }
    if (sum > 1) for (const v of VOWELS) this.w[v] /= sum;
    return this.w;
  }
}

// Realtime: tap any Web Audio node (the WebRTC remote track or a mock <audio>).
export class LiveAnalyser {
  constructor(ctx, source) {
    this.node = ctx.createAnalyser();
    this.node.fftSize = 1024;
    this.node.smoothingTimeConstant = 0;
    source.connect(this.node);
    this.td = new Float32Array(this.node.fftSize);
    this.fd = new Float32Array(this.node.frequencyBinCount);
    this.binHz = ctx.sampleRate / this.node.fftSize;
  }
  sample() {
    this.node.getFloatTimeDomainData(this.td);
    this.node.getFloatFrequencyData(this.fd);
    const mag = this.fd.map((db) => Math.pow(10, db / 20));
    return featuresFrom(this.td, mag, this.binHz);
  }
}

// Offline: pre-analyse a decoded clip into a feature track (capture mode, tests).
export function analyseSamples(samples, sampleRate, fps = 60, n = 1024) {
  const hop = sampleRate / fps;
  const frames = [];
  const re = new Float32Array(n), im = new Float32Array(n), mag = new Float32Array(n / 2);
  for (let c = 0; c + n / 2 < samples.length + n / 2; c += hop) {
    const start = Math.round(c - n / 2);
    for (let i = 0; i < n; i++) {
      const s = samples[start + i] ?? 0;
      re[i] = s * (0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1)));
      im[i] = 0;
    }
    const td = re.slice();
    fft(re, im);
    for (let i = 0; i < n / 2; i++) mag[i] = Math.hypot(re[i], im[i]);
    // The Hann window costs ~4.3 dB of RMS; add it back so live and offline agree.
    const f = featuresFrom(td, mag, sampleRate / n);
    f.amp = clamp((f.db + 4.3 + 40) / 20);
    frames.push(f);
  }
  return { fps, frames };
}

export const featureAt = (track, t) => (t < 0 ? null : track.frames[Math.floor(t * track.fps)] ?? null);

function fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k, b = a + len / 2;
        const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr; im[b] = im[a] - ti;
        re[a] += tr; im[a] += ti;
        const ncr = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = ncr;
      }
    }
  }
}

// ---- HeadAudio ----------------------------------------------------------------
// MFCC + Gaussian prototypes + Mahalanobis distance, 32 ms frames every 16 ms at
// 16 kHz. We use its per-frame prototype distances, not its own eased blendshape
// output (a 6-frame majority vote plus 100 ms ramps, ~100 ms behind the audio),
// and turn them into viseme probabilities with visemeProbs(). Live (AudioWorklet)
// and offline (the same Processor run on the main thread over a decoded clip)
// both go through VisemeStream, so there is one mapping.

// HeadAudio's viseme ids 0..14, in order.
export const VISEMES = ["aa", "E", "ih", "oh", "ou", "PP", "SS", "TH", "DD", "FF", "kk", "nn", "RR", "CH", "sil"];
export const SIL = 14;
const HA = new URL("../vendor/headaudio/", import.meta.url);
export const HA_HOP = 256 / 16000; // 16 ms
// VAD gate in HeadAudio's pre-emphasised log-energy units (not dBFS).
export const HA_PARAMS = { vadGateActiveDb: -45, vadGateInactiveDb: -55, speakerMeanHz: 150, silSensitivity: 1.2 };
// Softmax temperature over squared Mahalanobis distances (12-D, so d ~ 12 +- 5).
export const HA_TEMP = 3;
// Score with the full Gaussian log-likelihood (adds each prototype's log-determinant).
export const HA_LOGDET = false;

let modelP = null;
export function loadVisemeModel() {
  modelP ??= (async () => {
    const { Training } = await import(new URL("modules/training.mjs", HA).href);
    const buffer = await fetch(new URL("dist/model-en-mixed.bin", HA)).then((r) => {
      if (!r.ok) throw new Error(`headaudio model ${r.status}`);
      return r.arrayBuffer();
    });
    return parseVisemeModel(buffer, Training);
  })();
  modelP.catch(() => (modelP = null));
  return modelP;
}

// 368-byte records: header, mu[12], lower-triangular inverse covariance[78].
export function parseVisemeModel(buffer, Training) {
  const tr = new Training();
  const model = [];
  for (let pos = 0; pos + 368 <= buffer.byteLength; pos += 368) model.push(tr.decodeBinaryRecord(new Float32Array(buffer, pos, 92)));
  if (!model.length) throw new Error("headaudio model empty");
  return { model, visemeOf: Uint8Array.from(model, (p) => p.viseme), logdet: Float32Array.from(model, (p) => logdetLower(p.sigmaInvLower)), buffer };
}

// log|S| of a 12x12 SPD matrix stored lower-triangular (row-major), via Cholesky.
function logdetLower(low, n = 12) {
  const a = Array.from({ length: n }, () => new Float64Array(n));
  for (let i = 0, k = 0; i < n; i++) for (let j = 0; j <= i; j++, k++) a[i][j] = a[j][i] = low[k];
  let ld = 0;
  for (let j = 0; j < n; j++) {
    let d = a[j][j];
    for (let k = 0; k < j; k++) d -= a[j][k] * a[j][k];
    if (d <= 0) return 0;
    const l = Math.sqrt(d);
    a[j][j] = l;
    ld += 2 * Math.log(l);
    for (let i = j + 1; i < n; i++) {
      let s = a[i][j];
      for (let k = 0; k < j; k++) s -= a[i][k] * a[j][k];
      a[i][j] = s / l;
    }
  }
  return ld;
}

// Prototype distances -> probability per viseme (min distance per viseme, softmax).
// With `logdet` (log|inverse covariance| per prototype) the score is the full Gaussian
// log-likelihood, d - log|S^-1|, rather than HeadAudio's bare Mahalanobis distance.
export function visemeProbs(distances, visemeOf, temp = HA_TEMP, out = new Float32Array(15), logdet = null) {
  out.fill(Infinity);
  for (let i = 0; i < distances.length; i++) {
    const d = logdet ? distances[i] - logdet[i] : distances[i];
    if (d < out[visemeOf[i]]) out[visemeOf[i]] = d;
  }
  let m = Infinity;
  for (let v = 0; v < 15; v++) m = Math.min(m, out[v]);
  let z = 0;
  for (let v = 0; v < 15; v++) z += out[v] = Number.isFinite(out[v]) ? Math.exp((m - out[v]) / temp) : 0;
  for (let v = 0; v < 15; v++) out[v] /= z || 1;
  return out;
}

// Folds processor messages into viseme frames: {t, p: Float32Array(15), vote}.
// A frame of silence (VAD closed, or "ended") is null.
export class VisemeStream {
  constructor({ visemeOf, logdet }, { temp = HA_TEMP, useLogdet = HA_LOGDET } = {}) {
    Object.assign(this, { visemeOf, temp, logdet: useLogdet ? logdet : null });
    this.current = null;
    this.onframe = null;
  }
  message(d) {
    if (d.event === "viseme" && d.distances?.length) {
      this.current = { t: d.t, p: visemeProbs(d.distances, this.visemeOf, this.temp, undefined, this.logdet), vote: d.viseme ?? this.current?.vote ?? SIL };
      this.onframe?.(this.current);
    } else if (d.event === "ended") {
      this.current = null;
      this.onframe?.({ t: d.t, p: null, vote: SIL });
    }
  }
}

// Offline: run HeadAudio's processor over a decoded clip, in 128-sample blocks as
// the worklet would. Frames are keyed by audio time (frame centre), so capture
// can step a virtual clock at any rate. ~0.3 s per minute of 48 kHz audio.
export async function analyseVisemes(samples, sampleRate, params = HA_PARAMS) {
  const [M, { Processor }] = await Promise.all([loadVisemeModel(), import(new URL("modules/processor.mjs", HA).href)]);
  return runVisemes(Processor, M, samples, sampleRate, params);
}

export function runVisemes(Processor, M, samples, sampleRate, params = HA_PARAMS, opts = {}) {
  const frames = new Array(Math.ceil(samples.length / sampleRate / HA_HOP) + 2).fill(null);
  const stream = new VisemeStream(M, opts);
  stream.onframe = (f) => {
    const i = Math.round(f.t / HA_HOP);
    if (i >= 0 && i < frames.length) frames[i] = f.p ? f : null;
  };
  const port = { postMessage: (m) => stream.message(m) };
  const proc = new Processor({ sampleRate, processorOptions: { visemeEventsEnabled: true }, parameterData: params }, { port });
  proc.update({ ...params, timerReset: 0 });
  proc._onmessage({ data: { event: "model", model: M.model } });
  for (let i = 0; i < samples.length; i += 128) proc.process(samples.subarray(i, Math.min(samples.length, i + 128)));
  return { hop: HA_HOP, frames };
}

export const visemeAt = (track, t) => (track && t >= 0 ? track.frames[Math.round(t / track.hop)] ?? null : null);

// Realtime: HeadAudio's AudioWorklet on any Web Audio node (the WebRTC remote track).
export class LiveVisemes {
  static async create(ctx, source, params = HA_PARAMS) {
    const [M, { HeadAudio }] = await Promise.all([loadVisemeModel(), import(new URL("modules/headaudio.mjs", HA).href)]);
    await ctx.audioWorklet.addModule(new URL("dist/headworklet.min.mjs", HA).href);
    const node = new HeadAudio(ctx, { processorOptions: { visemeEventsEnabled: true }, parameterData: params });
    const lv = new LiveVisemes(node, M);
    node.onviseme = (d) => lv.stream.message(d);
    node.onended = (d) => lv.stream.message(d);
    await node.loadModel(new URL("dist/model-en-mixed.bin", HA).href); // same file, same prototype order
    source.connect(node);
    return lv;
  }
  constructor(node, M) {
    this.node = node;
    this.stream = new VisemeStream(M);
    this.at = 0;
    this.stream.onframe = () => (this.at = performance.now());
  }
  // Latest frame, or null once the processor has gone quiet for 60 ms.
  sample() {
    return this.stream.current && performance.now() - this.at < 60 ? this.stream.current : null;
  }
}

// ---- both providers, one feature frame ----------------------------------------
// Offline: a decoded clip -> {energy, visemes|null}. HeadAudio failing to load
// leaves the energy provider alone in charge.
export async function analyseClip(samples, sampleRate) {
  const energy = analyseSamples(samples, sampleRate, 60);
  let visemes = null;
  if (!FORCE_ENERGY) {
    try {
      visemes = await analyseVisemes(samples, sampleRate);
    } catch (e) {
      console.warn("lipsync: HeadAudio unavailable, using the energy provider", e);
    }
  }
  return { energy, visemes };
}

export function clipFeatureAt(track, t) {
  const f = featureAt(track.energy, t);
  if (!f || !track.visemes) return f;
  return { ...f, ha: true, visemes: visemeAt(track.visemes, t) };
}

// Realtime: energy analyser now, HeadAudio worklet once it has loaded (if it does).
export class LiveMouth {
  constructor(ctx, source) {
    this.energy = new LiveAnalyser(ctx, source);
    this.visemes = null;
    if (!FORCE_ENERGY)
      LiveVisemes.create(ctx, source)
        .then((v) => (this.visemes = v))
        .catch((e) => console.warn("lipsync: HeadAudio unavailable, using the energy provider", e));
  }
  sample() {
    const f = this.energy.sample();
    return this.visemes ? { ...f, ha: true, visemes: this.visemes.sample() } : f;
  }
}
