import { VRMExpression, VRMExpressionMorphTargetBind } from "@pixiv/three-vrm";
import { clamp } from "./bus.js";
import { VOWELS, VISEMES } from "./lipsync.js";

// Mouth rendering: feature frames from lipsync.js -> avatar mouth weights.
// VTuber-style: winner-take-most over visemes, loudness-scaled aperture, silence
// gate, asymmetric smoothing lerped from the displayed weight, fast bilabial closure.
//
//   const lips = createMouth(vrm);
//   const w = lips.update(features, now, dt); // {aa, ih, ou, ee, oh, open, viseme}
//   lips.apply(vrm.expressionManager, w, faceWeights);
//
// Output channels are the five VRM vowel presets. On VRoid avatars they drive the raw
// Face morphs (Fcl_MTH_A/I/U/E/O, plus Close/Small when the export has them)
// through custom expressions, and the vowel presets are held at 0 so nothing fights.
// Every mouth write goes through the expression manager, in one place.

// Viseme -> pose over the vowel channels. Consonants stay <= 0.25: they shade the
// shape between vowels rather than flap the jaw.
const POSE = {
  aa: { aa: 1.0 },
  E: { ee: 0.7, aa: 0.3 }, // VRoid's E alone reads as a slit; give it some jaw
  ih: { ih: 0.8, ee: 0.1 },
  oh: { oh: 0.9, aa: 0.1 },
  ou: { ou: 0.95 },
  PP: {}, // bilabial closure
  FF: { ih: 0.1 }, // lower lip to teeth: nearly closed
  TH: { ee: 0.22, aa: 0.08 },
  DD: { ee: 0.2, ih: 0.12 },
  kk: { ee: 0.18, aa: 0.14 },
  CH: { ou: 0.22, ih: 0.15 },
  SS: { ih: 0.25, ee: 0.06 },
  nn: { ih: 0.14, ee: 0.1 },
  RR: { ou: 0.22, oh: 0.12 },
  sil: {},
};
const VOWEL_OF = { aa: "aa", ih: "ih", ou: "ou", ee: "E", oh: "oh" }; // energy-provider vowels -> visemes
const CLOSING = new Set(["PP", "FF", "sil"]);
const IS_VOWEL = new Set(["aa", "E", "ih", "oh", "ou"]);

// Tuned on lipsync-eval/ (see its README): time constants in seconds at smoothing = 1.
export const MOUTH = {
  attack: 0.025, // opening
  release: 0.07, // shape changes and relaxing
  close: 0.018, // PP/FF/silence: lips shut fast, the closure has to read
  second: 0.35, // runner-up viseme share (winner-take-most)
  pool: 0.03, // viseme probabilities are pooled (EMA) this long before ranking
  ppThresh: 0.5, // PP wins when its probability reaches this fraction of the winner's
  vowelBias: 1, // loud frames favour vowels: p(vowel) x (1 + vowelBias x amp)
  gate: 0.04, // amp below this is silence (~ -39 dBFS)
  whisper: 0.45, // aperture at the gate; loud speech reaches 1
  hold: 0.16, // "speaking" stays on this long after the last voiced frame
  talkOn: 0.12, // emotion -> talking-face crossfade
  talkOff: 0.35,
};

// Per-avatar gains (by VRM meta title/name), vowel channels, plus "open" overall.
export const GAINS = {
  default: { aa: 1, ih: 0.9, ou: 0.9, ee: 0.9, oh: 0.95, open: 1 },
  // Open vowels boosted after review: the tuned mapping read small on camera.
  AvatarSample_A: { aa: 1.15, ih: 0.9, ou: 1.05, ee: 1.0, oh: 1.1, open: 1.12 },
};

// While talking, VRoid's mouth-including emotion shapes (Fcl_ALL_*) hand over to
// their brow + eye parts, and the mouth keeps a closed-lip hint of the feeling, so
// a happy face can still shut its lips on "m" and "p".
const TALK_MOUTH = { happy: ["Fun", 0.5], relaxed: ["Fun", 0.4], sad: ["Sorrow", 0.3], angry: ["Angry", 0.5], surprised: null };
const EMOTIONS = ["happy", "angry", "sad", "relaxed", "surprised"];

function morphIndex() {
  const out = new Map(); // short name -> [{mesh, index}]
  return {
    add(mesh) {
      const names = mesh.geometry?.userData?.targetNames ?? Object.keys(mesh.morphTargetDictionary ?? {});
      names.forEach((n, i) => {
        const m = /Fcl_[A-Za-z]+_[A-Za-z_]+$/.exec(n);
        if (!m) return;
        const k = m[0].toLowerCase();
        out.set(k, [...(out.get(k) ?? []), { mesh, index: i }]);
      });
    },
    get: (short) => out.get(short.toLowerCase()) ?? null,
  };
}

export function createMouth(vrm, { smoothing = 1, gains } = {}) {
  const em = vrm?.expressionManager;
  const title = vrm?.meta?.title ?? vrm?.meta?.name;
  const gain = { ...GAINS.default, ...(GAINS[title] ?? {}), ...(gains ?? {}) };

  // Raw VRoid morphs, registered as custom expressions so the manager applies them.
  const morphs = morphIndex();
  vrm?.scene?.traverse((o) => o.morphTargetInfluences && morphs.add(o));
  const custom = (name, binds) => {
    const e = new VRMExpression(name);
    for (const [targets, weight] of binds) for (const t of targets ?? []) e.addBind(new VRMExpressionMorphTargetBind({ primitives: [t.mesh], index: t.index, weight }));
    if (!e.binds.length) return null;
    em.registerExpression(e);
    vrm.scene.add(e);
    return name;
  };
  const raw = {};
  const vroid = !!(em && morphs.get("Fcl_MTH_A") && morphs.get("Fcl_MTH_I"));
  if (vroid) {
    for (const [ch, m] of [["aa", "A"], ["ih", "I"], ["ou", "U"], ["ee", "E"], ["oh", "O"], ["close", "Close"], ["small", "Small"]])
      raw[ch] = custom(`lips_${ch}`, [[morphs.get(`Fcl_MTH_${m}`), 1]]);
  }
  // Talking-face split for mouth-including emotions (VRoid only).
  const split = {};
  if (vroid) {
    const alias = (p) => (em.getExpression(p) ? p : em.getExpression(p[0].toUpperCase() + p.slice(1)) ? p[0].toUpperCase() + p.slice(1) : null);
    for (const p of EMOTIONS) {
      const name = alias(p);
      const bind = name && em.getExpression(name).binds.find((b) => b.primitives && b.index != null);
      const tn = bind?.primitives[0]?.geometry?.userData?.targetNames?.[bind.index] ?? "";
      const all = /Fcl_ALL_([A-Za-z]+)$/.exec(tn)?.[1];
      if (!all) continue;
      const face = custom(`lips_${p}_face`, [[morphs.get(`Fcl_BRW_${all}`), 1], [morphs.get(`Fcl_EYE_${all}`), 1]]);
      const tm = TALK_MOUTH[p];
      const mouth = tm && custom(`lips_${p}_mouth`, [[morphs.get(`Fcl_MTH_${tm[0]}`), tm[1]]]);
      if (face) split[p] = { name, face, mouth };
    }
  }

  const w = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0, open: 0, viseme: "sil" };
  const x = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0, close: 0 }; // displayed
  const target = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0, close: 0 };
  let lastVoiced = -Infinity;
  const pool = new Float32Array(15);
  let pooled = false;
  let talk = 0;

  const rig = {
    vroid,
    speaking: false,
    smoothing,
    weights: w,

    // f: {amp, db, vowels?, visemes?: {p: Float32Array(15)}} or null (silence).
    update(f, now, dt) {
      const voiced = !!f && f.amp > MOUTH.gate;
      if (voiced) lastVoiced = now;
      else pooled = false;
      rig.speaking = now - lastVoiced < MOUTH.hold;
      for (const k in target) target[k] = 0;

      let top = "sil";
      if (voiced) {
        // Rank candidates: HeadAudio's 15 visemes, or the energy provider's 5 vowels.
        let r1 = "sil", p1 = 0, r2 = null, p2 = 0;
        const consider = (v, p) => {
          if (p > p1) (r2 = r1), (p2 = p1), (r1 = v), (p1 = p);
          else if (p > p2) (r2 = v), (p2 = p);
        };
        if (f.visemes?.p) {
          const k = pooled ? 1 - Math.exp(-dt / Math.max(1e-3, MOUTH.pool * rig.smoothing)) : 1;
          for (let i = 0; i < 15; i++) pool[i] += (f.visemes.p[i] * (i < 5 ? 1 + MOUTH.vowelBias * f.amp : 1) - pool[i]) * k;
          pooled = true;
          VISEMES.forEach((v, i) => consider(v, pool[i]));
          // A likely bilabial shuts the lips even when it does not win outright.
          if (r1 !== "PP" && pool[5] >= MOUTH.ppThresh * p1) (r2 = r1), (p2 = p1), (r1 = "PP"), (p1 = pool[5]);
        } else if (f.vowels) for (const v of VOWELS) consider(VOWEL_OF[v], f.vowels[v]);
        top = r1;
        const loud = MOUTH.whisper + (1 - MOUTH.whisper) * clamp((f.amp - MOUTH.gate) / (1 - MOUTH.gate));
        const add = (v, share) => {
          const scale = IS_VOWEL.has(v) ? loud : 0.7 + 0.3 * loud;
          for (const ch in POSE[v]) target[ch] += POSE[v][ch] * share * scale;
        };
        add(r1, 1);
        // A consonant winner keeps a trace of the vowel beside it; closures stay shut.
        if (r2 && !CLOSING.has(r1) && p1 > 0) add(r2, MOUTH.second * (p2 / p1));
        if (r1 === "PP") target.close = 1;
      }

      // Asymmetric smoothing, lerped from what is displayed.
      const s = Math.max(1e-3, rig.smoothing);
      const kOf = (tau) => 1 - Math.exp(-dt / (tau * s));
      const closing = CLOSING.has(top);
      let sum = 0;
      for (const ch in x) {
        const tau = target[ch] > x[ch] ? MOUTH.attack : closing ? MOUTH.close : MOUTH.release;
        x[ch] += (target[ch] - x[ch]) * kOf(tau);
        if (ch !== "close") sum += x[ch];
      }
      const norm = sum > 1 ? 1 / sum : 1;
      let open = 0;
      for (const v of VOWELS) open += w[v] = clamp(x[v] * norm * gain[v] * gain.open);
      w.open = clamp(open);
      w.close = x.close;
      w.viseme = top;

      talk += ((rig.speaking ? 1 : 0) - talk) * (1 - Math.exp(-dt / (rig.speaking ? MOUTH.talkOn : MOUTH.talkOff)));
      return w;
    },

    // Write the mouth (and, on VRoid, the talking-face split) into the expression manager.
    apply(mgr = em, weights = w, faceW = null) {
      if (!mgr) return;
      for (const v of VOWELS) {
        if (raw[v]) {
          mgr.setValue(raw[v], weights[v] ?? 0);
          mgr.setValue(v, 0);
        } else mgr.setValue(v, weights[v] ?? 0);
      }
      if (raw.close) mgr.setValue(raw.close, 0.6 * (weights.close ?? 0));
      if (raw.small) mgr.setValue(raw.small, 0.5 * ((weights.ou ?? 0) + 0.5 * (weights.oh ?? 0)));
      if (faceW) {
        for (const p in split) {
          const e = faceW[p] ?? 0, sp = split[p];
          mgr.setValue(sp.name, e * (1 - talk));
          mgr.setValue(sp.face, e * talk);
          if (sp.mouth) mgr.setValue(sp.mouth, e * talk);
        }
      }
    },
  };
  return rig;
}
