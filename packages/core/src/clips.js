import * as THREE from "three";

// annie-core: a procedural motion pack, authored here in code and released CC0.
// Poses are Euler XYZ degrees on VRM *normalized* humanoid bones (rest = T-pose,
// character faces +Z). Conventions, derived once so clips read as intent:
//   upper arm z: lowers the arm (left negative, right positive); x negative raises forward
//   lower arm y: bends the elbow forward (left negative, right positive)
//   lower arm z: bends the elbow up (left positive, right negative)
//   head/neck/spine x positive: pitch down/forward; head y positive: turn to her left
//   upper leg x negative: thigh forward; lower leg x positive: knee bend
// Each clip is a function of time sampled at 30 fps into a THREE.AnimationClip that
// carries tracks for every bone below, so crossfades never leave a bone stranded.

const BONES = [
  "hips", "spine", "chest", "upperChest", "neck", "head",
  "leftShoulder", "rightShoulder", "leftUpperArm", "rightUpperArm",
  "leftLowerArm", "rightLowerArm", "leftHand", "rightHand",
  "leftUpperLeg", "rightUpperLeg", "leftLowerLeg", "rightLowerLeg", "leftFoot", "rightFoot",
];

export const REST = {
  spine: [2, 0, 0], chest: [2, 0, 0], head: [-2, 0, 0],
  leftUpperArm: [-5, 0, -77], rightUpperArm: [-5, 0, 77],
  leftLowerArm: [0, -14, 0], rightLowerArm: [0, 14, 0],
  leftHand: [0, 0, -6], rightHand: [0, 0, 6],
};

const TAU = Math.PI * 2;
const sin = (t, hz, ph = 0) => Math.sin(TAU * hz * t + ph);
const clamp01 = (v) => Math.min(1, Math.max(0, v));
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
// 0→1 over [0,a], hold, 1→0 over [d-b, d]
const env = (t, d, a = 0.35, b = 0.45) => ease(clamp01(t / a)) * ease(clamp01((d - t) / b));

function mirror(p) {
  const out = {};
  for (const [k, v] of Object.entries(p)) {
    const m = k.startsWith("left") ? "right" + k.slice(4) : k.startsWith("right") ? "left" + k.slice(5) : k;
    out[m] = m === k ? v : [v[0], -v[1], -v[2]];
  }
  return out;
}
const both = (p) => ({ ...p, ...mirror(p) });

// Blend a target pose over REST by weight w (degree-space lerp is fine at these angles).
function over(target, w, base = REST) {
  const out = {};
  for (const b of new Set([...Object.keys(base), ...Object.keys(target)])) {
    const r = base[b] || [0, 0, 0];
    const t = target[b] || r;
    out[b] = [0, 1, 2].map((i) => r[i] + (t[i] - r[i]) * w);
  }
  return out;
}
const add = (p, extra) => {
  const out = { ...p };
  for (const [b, v] of Object.entries(extra)) {
    const r = out[b] || [0, 0, 0];
    out[b] = [r[0] + v[0], r[1] + v[1], r[2] + v[2]];
  }
  return out;
};

// ---- the pack ---------------------------------------------------------------
// `when` is the sentence that becomes Jev's criteria for this clip.
export const CLIPS = {
  idle_a: {
    loop: true, duration: 6,
    when: "calm standing idle",
    f: (t) => ({
      pose: add(REST, {
        hips: [0, 0, 1.6 * sin(t, 1 / 6)], spine: [0, 0, -1.2 * sin(t, 1 / 6)],
        head: [0, 2 * sin(t, 1 / 6, 1), 1.5 * sin(t, 1 / 6)],
        leftUpperArm: [0, 0, 1.5 * sin(t, 1 / 6)], rightUpperArm: [0, 0, 1.5 * sin(t, 1 / 6)],
      }),
      hips: [0.006 * sin(t, 1 / 6), 0, 0],
    }),
  },
  idle_b: {
    loop: true, duration: 8,
    when: "relaxed idle, glancing around",
    f: (t) => ({
      pose: add(REST, {
        hips: [0, 3 * sin(t, 1 / 8), 0], chest: [0, -2 * sin(t, 1 / 8), 0],
        head: [1.5 * sin(t, 1 / 4), 7 * sin(t, 1 / 8, 0.8), -2 * sin(t, 1 / 8)],
        leftUpperArm: [-3 * sin(t, 1 / 8), 0, 0], rightUpperArm: [3 * sin(t, 1 / 8), 0, 0],
      }),
      hips: [0, 0, 0],
    }),
  },
  idle_c: {
    loop: true, duration: 6,
    when: "idle with hands loosely together in front",
    f: (t) => ({
      pose: add(over(both({ leftUpperArm: [-22, -22, -66], leftLowerArm: [0, -62, 0], leftHand: [0, -10, -4] }), 1), {
        hips: [0, 0, -1.8 * sin(t, 1 / 6)], spine: [0, 0, 1.4 * sin(t, 1 / 6)],
        head: [0, -3 * sin(t, 1 / 6, 2), 2.5 * sin(t, 1 / 6, 0.5)],
      }),
      hips: [-0.006 * sin(t, 1 / 6), 0, 0],
    }),
  },
  wave: {
    duration: 2.6,
    when: "greeting hello or goodbye with a raised hand",
    f: (t, d) => {
      const w = env(t, d, 0.4, 0.5);
      const osc = sin(t, 2.6) * clamp01((t - 0.35) / 0.2);
      return {
        pose: add(over({ rightUpperArm: [-25, 0, 18], rightLowerArm: [0, 20, -92], rightHand: [0, 0, 0], head: [-3, -4, 5] }, w), {
          rightLowerArm: [0, 0, 16 * osc * w], rightHand: [0, 0, 10 * osc * w], spine: [0, 0, 2 * w],
        }),
      };
    },
  },
  excited_bounce: {
    duration: 1.9,
    when: "hyped, can't contain excitement, 'yes!'",
    f: (t, d) => {
      const w = env(t, d, 0.25, 0.45);
      const b = Math.max(0, sin(t, 2.2)) * w;
      return {
        pose: add(over(both({ leftUpperArm: [-30, 0, -38], leftLowerArm: [0, -30, 95], leftHand: [0, 0, -10] }), w), {
          spine: [-3 * b, 0, 0], head: [-6 * b, 0, 0],
          leftUpperLeg: [-10 * b, 0, 0], rightUpperLeg: [-10 * b, 0, 0],
          leftLowerLeg: [20 * b, 0, 0], rightLowerLeg: [20 * b, 0, 0], leftFoot: [-10 * b, 0, 0], rightFoot: [-10 * b, 0, 0],
        }),
        hips: [0, -0.045 * b + 0.02 * Math.max(0, sin(t, 2.2, Math.PI)) * w, 0],
      };
    },
  },
  clap: {
    duration: 2.3,
    when: "applause, great news, celebrating what someone said",
    f: (t, d) => {
      const w = env(t, d, 0.35, 0.45);
      const c = (0.5 + 0.5 * sin(t, 3.2)) * clamp01((t - 0.3) / 0.15);
      return {
        pose: add(over(both({ leftUpperArm: [-38, -38, -58], leftLowerArm: [0, -88, 0], leftHand: [0, 0, -8], head: [-4, 0, 0] }), w), {
          leftUpperArm: [0, 10 * c * w, 0], rightUpperArm: [0, -10 * c * w, 0], spine: [2 * c * w, 0, 0],
        }),
        hips: [0, -0.008 * c * w, 0],
      };
    },
  },
  dance: {
    kind: "action", // needs an explicit spoken commitment; everything else is an expressive gesture
    duration: 6.4,
    when: "dancing, celebrating to music, party",
    f: (t, d) => {
      const w = env(t, d, 0.4, 0.6);
      const beat = 2; // 120 bpm
      const side = sin(t, beat / 2);
      const bob = Math.abs(sin(t, beat / 2));
      const pump = clamp01((t - 3.2) / 0.3); // second half: raise the roof
      const swing = sin(t, beat / 2);
      const armsA = both({ leftUpperArm: [-10, 0, -58], leftLowerArm: [0, -80, 0] });
      const armsB = both({ leftUpperArm: [-10, 0, -20], leftLowerArm: [0, 0, 100], leftHand: [-20, 0, 0] });
      const arms = over(armsB, pump, over(armsA, 1));
      return {
        pose: add(over(arms, w), {
          hips: [0, 8 * swing * w, 7 * side * w], spine: [0, -4 * swing * w, -8 * side * w], chest: [0, 0, -4 * side * w],
          head: [4 * bob * w, 6 * swing * w, 7 * side * w],
          leftUpperArm: [28 * swing * w * (1 - pump), 0, 14 * bob * pump * w],
          rightUpperArm: [-28 * swing * w * (1 - pump), 0, -14 * bob * pump * w],
          leftUpperLeg: [-12 * bob * w, 0, 0], rightUpperLeg: [-12 * bob * w, 0, 0],
          leftLowerLeg: [22 * bob * w, 0, 0], rightLowerLeg: [22 * bob * w, 0, 0],
          leftFoot: [-10 * bob * w, 0, 0], rightFoot: [-10 * bob * w, 0, 0],
        }),
        hips: [0.035 * side * w, -0.04 * bob * w, 0],
      };
    },
  },
  sad_slump: {
    duration: 3.4,
    when: "deflated by bad news, grief, feeling low",
    f: (t, d) => {
      const w = env(t, d, 0.8, 0.8);
      return {
        pose: over(both({ leftUpperArm: [-2, 0, -80], leftLowerArm: [0, -6, 0], leftShoulder: [0, 0, -6], spine: [9, 0, 0], chest: [5, 0, 0], head: [16, 0, -5], neck: [4, 0, 0] }), w),
        hips: [0, -0.01 * w, 0],
      };
    },
  },
  shrug: {
    duration: 1.8,
    when: "not knowing, 'who knows', playful 'oh well'",
    f: (t, d) => {
      const w = env(t, d, 0.3, 0.5);
      return {
        pose: over(both({ leftShoulder: [0, 0, 14], leftUpperArm: [-20, 28, -62], leftLowerArm: [0, -78, 0], leftHand: [-35, 0, 0], head: [0, 0, 9] }), w),
      };
    },
  },
  think: {
    duration: 3.2,
    when: "pondering, working out an answer, explaining how something works",
    f: (t, d) => {
      const w = env(t, d, 0.5, 0.6);
      return {
        pose: over({
          rightUpperArm: [-38, -8, 62], rightLowerArm: [0, 128, 0], rightHand: [-10, 0, -20],
          leftUpperArm: [-18, -40, -70], leftLowerArm: [0, -92, 0], leftHand: [0, -10, 0],
          head: [-6, -8, -7], spine: [2, 0, 0],
        }, w),
      };
    },
  },
  laugh: {
    duration: 1.9,
    when: "laughing out loud at something funny",
    f: (t, d) => {
      const w = env(t, d, 0.2, 0.5);
      const s = Math.abs(sin(t, 2.6)) * w;
      return {
        pose: add(over({ leftUpperArm: [-25, -30, -68], leftLowerArm: [0, -75, 0], head: [-10, 0, 4], spine: [-3, 0, 0] }, w), {
          spine: [6 * s, 0, 0], head: [5 * s, 0, 0], leftShoulder: [0, 0, 4 * s], rightShoulder: [0, 0, -4 * s],
        }),
        hips: [0, -0.006 * s, 0],
      };
    },
  },
  surprised_recoil: {
    duration: 1.5,
    when: "startled, taken aback, interrupted",
    f: (t, d) => {
      const w = env(t, d, 0.12, 0.6);
      return {
        pose: over(both({ spine: [-8, 0, 0], head: [-7, 0, 0], leftUpperArm: [-32, -12, -52], leftLowerArm: [0, -105, 0], leftHand: [-20, 0, 0] }), w),
        hips: [0, 0, -0.02 * w],
      };
    },
  },
  bow: {
    duration: 2.6,
    when: "polite bow, thank you, apology",
    f: (t, d) => {
      const w = env(t, d, 0.7, 0.8);
      return {
        pose: over(both({ spine: [22, 0, 0], chest: [8, 0, 0], head: [12, 0, 0], leftUpperArm: [-12, -18, -74], leftLowerArm: [0, -40, 0] }), w),
      };
    },
  },
  point_self: {
    duration: 1.9,
    when: "talking about herself, 'me?', 'I'm Annie'",
    f: (t, d) => {
      const w = env(t, d, 0.35, 0.5);
      return {
        pose: over({ rightUpperArm: [-30, -20, 64], rightLowerArm: [0, 118, 0], rightHand: [0, 0, -15], head: [-2, 0, 4] }, w),
      };
    },
  },
};

export const IDLE_POOL = ["idle_a", "idle_b", "idle_c"];
export const ACTION_CLIPS = Object.keys(CLIPS).filter((k) => !IDLE_POOL.includes(k));

const D2R = Math.PI / 180;

// Bake every clip for one VRM. Returns {name: THREE.AnimationClip}.
export function buildClips(vrm, fps = 30) {
  const h = vrm.humanoid;
  const nodes = {};
  for (const b of BONES) {
    const n = h.getNormalizedBoneNode(b);
    if (n) nodes[b] = n;
  }
  const hipsRest = nodes.hips.position.clone();
  // Poses are authored in VRM 1.0 space; 0.x rigs face -Z, so x and z flip (as three-vrm-animation does).
  const flip = vrm.meta?.metaVersion === "0" ? -1 : 1;
  const e = new THREE.Euler();
  const q = new THREE.Quaternion();
  const out = {};
  for (const [name, def] of Object.entries(CLIPS)) {
    const n = Math.max(2, Math.round(def.duration * fps) + 1);
    const times = new Float32Array(n);
    const rot = Object.fromEntries(Object.keys(nodes).map((b) => [b, new Float32Array(n * 4)]));
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const t = (i / (n - 1)) * def.duration;
      times[i] = t;
      const { pose, hips = [0, 0, 0] } = def.f(t, def.duration);
      for (const b of Object.keys(nodes)) {
        const r = pose[b] || [0, 0, 0];
        e.set(r[0] * D2R, r[1] * D2R, r[2] * D2R, "XYZ");
        q.setFromEuler(e);
        if (flip < 0) (q.x = -q.x), (q.z = -q.z);
        q.toArray(rot[b], i * 4);
      }
      pos[i * 3] = hipsRest.x + flip * hips[0];
      pos[i * 3 + 1] = hipsRest.y + hips[1];
      pos[i * 3 + 2] = hipsRest.z + flip * hips[2];
    }
    const tracks = Object.keys(nodes).map((b) => new THREE.QuaternionKeyframeTrack(`${nodes[b].name}.quaternion`, times, rot[b]));
    tracks.push(new THREE.VectorKeyframeTrack(`${nodes.hips.name}.position`, times, pos));
    out[name] = new THREE.AnimationClip(name, def.duration, tracks);
  }
  return out;
}
