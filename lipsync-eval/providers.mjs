// Runs the mouth pipelines exactly as the stage does in capture mode, frame-stepped on a
// virtual clock, and dumps per-frame mouth weights for score.py.
//   node --import ./register.mjs providers.mjs clips.json out.json [config.json]
// Pipelines: "before"  = energy provider + old MouthShaper (the pre-upgrade mouth)
//            "energy"  = energy provider + mouth.js (the fallback path)
//            "ha"      = HeadAudio visemes + energy loudness + mouth.js (the new default)
import { readFileSync, writeFileSync } from "node:fs";
import * as L from "../packages/core/src/lipsync.js";
import { createMouth, MOUTH } from "../packages/core/src/mouth.js";
import { Processor } from "../packages/core/vendor/headaudio/modules/processor.mjs";
import { Training } from "../packages/core/vendor/headaudio/modules/training.mjs";

const [, , clipsPath, outPath, cfgPath] = process.argv;
const clips = JSON.parse(readFileSync(clipsPath, "utf8"));
const cfg = cfgPath ? JSON.parse(readFileSync(cfgPath, "utf8")) : {};
const FPS = cfg.fps ?? 60;
const bin = readFileSync(new URL("../packages/core/vendor/headaudio/dist/model-en-mixed.bin", import.meta.url));
const M = L.parseVisemeModel(bin.buffer.slice(bin.byteOffset, bin.byteOffset + bin.byteLength), Training);

export function readWav(path) {
  const b = readFileSync(path);
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  let p = 12, fmt, data;
  while (p + 8 <= b.length) {
    const id = b.toString("ascii", p, p + 4), n = dv.getUint32(p + 4, true);
    if (id === "fmt ") fmt = { tag: dv.getUint16(p + 8, true), ch: dv.getUint16(p + 10, true), sr: dv.getUint32(p + 12, true), bits: dv.getUint16(p + 22, true) };
    if (id === "data") data = { off: p + 8, n: Math.min(n, b.length - p - 8) };
    p += 8 + n + (n & 1);
  }
  const bps = fmt.bits / 8, frames = Math.floor(data.n / (bps * fmt.ch));
  const out = new Float32Array(frames);
  for (let i = 0; i < frames; i++) {
    let s = 0;
    for (let c = 0; c < fmt.ch; c++) {
      const o = data.off + (i * fmt.ch + c) * bps;
      s += fmt.bits === 16 ? dv.getInt16(o, true) / 32768 : fmt.tag === 3 ? dv.getFloat32(o, true) : dv.getInt32(o, true) / 2 ** 31;
    }
    out[i] = s / fmt.ch;
  }
  return { samples: out, sr: fmt.sr };
}

Object.assign(MOUTH, cfg.mouth ?? {});
const haParams = { ...L.HA_PARAMS, ...(cfg.ha ?? {}) };
const haOpts = { temp: cfg.temp ?? L.HA_TEMP, useLogdet: cfg.logdet ?? L.HA_LOGDET };
const lead = cfg.lead ?? L.MOUTH_LEAD;
const leadBefore = 0.04;
const out = { fps: FPS, lead, cfg, clips: {} };
for (const c of clips) {
  const { samples, sr } = readWav(c.wav);
  const dur = samples.length / sr;
  const energy = L.analyseSamples(samples, sr, 60);
  const visemes = L.runVisemes(Processor, M, samples, sr, haParams, haOpts);
  const track = { energy, visemes };
  const old = new L.MouthShaper();
  const mE = createMouth(null, { smoothing: cfg.smoothing ?? 1 });
  const mH = createMouth(null, { smoothing: cfg.smoothing ?? 1 });
  const rec = { before: [], energy: [], ha: [], haViseme: [], dur };
  let last = 0;
  for (let i = 0; i / FPS <= dur + 0.3; i++) {
    const t = i / FPS, dt = i ? t - last : 1 / FPS;
    last = t;
    const w0 = old.update(L.featureAt(energy, t + leadBefore), t, dt);
    rec.before.push(L.VOWELS.map((v) => +w0[v].toFixed(4)));
    const w1 = mE.update(L.featureAt(energy, t + lead), t, dt);
    rec.energy.push(L.VOWELS.map((v) => +w1[v].toFixed(4)).concat([w1.viseme]));
    const w2 = mH.update(L.clipFeatureAt(track, t + lead), t, dt);
    rec.ha.push(L.VOWELS.map((v) => +w2[v].toFixed(4)).concat([w2.viseme]));
  }
  // Raw classifier output per 16 ms frame (centre time), for accuracy without rendering.
  rec.haFrames = visemes.frames.map((f, i) => (f ? [i * L.HA_HOP, f.vote, Array.from(f.p, (x) => +x.toFixed(3))] : [i * L.HA_HOP, null, null]));
  out.clips[c.id] = rec;
}
writeFileSync(outPath, JSON.stringify(out));
console.log("wrote", outPath, Object.keys(out.clips).length, "clips");
