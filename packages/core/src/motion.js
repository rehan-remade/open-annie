import * as THREE from "three";
import { GestureScheduler } from "./gestures.js";

// JSON motion packs (assets/clips/<pack>/): real human motion retargeted offline by
// the offline motion pipeline (motion/). Each clip is {fps, duration, loop, bones: {vrmBone: [x,y,z,w,...]},
// hips_position: [x,y,z,...]} with quaternions on three-vrm *normalized* humanoid bones in
// VRM 1.0 space (rest = T-pose, identity, facing +Z) and the hips offset relative to rest.
// Contract: docs/contracts.md.

export const FINGER_BONES = [];
for (const s of ["left", "right"]) {
  for (const p of ["Metacarpal", "Proximal", "Distal"]) FINGER_BONES.push(`${s}Thumb${p}`);
  for (const f of ["Index", "Middle", "Ring", "Little"]) for (const p of ["Proximal", "Intermediate", "Distal"]) FINGER_BONES.push(`${s}${f}${p}`);
}

export async function fetchPack(baseUrl, { only } = {}) {
  const base = baseUrl.endsWith("/") ? baseUrl : baseUrl + "/";
  const pack = await fetch(new URL("pack.json", base)).then((r) => {
    if (!r.ok) throw new Error(`pack ${base}: ${r.status}`);
    return r.json();
  });
  const entries = Object.entries(pack.clips).filter(([name]) => !only || only(name, pack.clips[name]));
  const data = await Promise.all(entries.map(([, c]) => fetch(new URL(c.file, base)).then((r) => r.json())));
  const clips = {};
  entries.forEach(([name, c], i) => (clips[name] = { ...c, name, data: data[i] }));
  return { ...pack, clips, base };
}

// VRM 0.x rigs face -Z: conjugating by a 180 deg turn about Y negates quaternion x and z
// and the hips x/z offset (the same flip three-vrm-animation applies, and clips.js uses).
function flipOf(vrm) {
  return vrm.meta?.metaVersion === "0" ? -1 : 1;
}

// Constant per-bone rotations as a plain object {bone: THREE.Quaternion} (already flipped).
export function poseFromData(vrm, bones) {
  const f = flipOf(vrm);
  const out = {};
  for (const [b, a] of Object.entries(bones)) out[b] = new THREE.Quaternion(f * a[0], a[1], f * a[2], a[3]);
  return out;
}

// One JSON clip -> THREE.AnimationClip bound to this VRM's normalized bone nodes.
// `fill` adds constant tracks for bones the clip doesn't drive (e.g. relaxed fingers).
export function toAnimationClip(vrm, name, data, { fill = {}, bones: keep } = {}) {
  const h = vrm.humanoid;
  const f = flipOf(vrm);
  const n = data.frames ?? Math.round(data.duration * data.fps) + 1;
  const times = new Float32Array(n);
  for (let i = 0; i < n; i++) times[i] = i / data.fps;
  const tracks = [];
  for (const [bone, arr] of Object.entries(data.bones)) {
    if (keep && !keep.has(bone)) continue;
    const node = h.getNormalizedBoneNode(bone);
    if (!node) continue;
    const v = new Float32Array(n * 4);
    for (let i = 0; i < n * 4; i += 4) {
      v[i] = f * arr[i];
      v[i + 1] = arr[i + 1];
      v[i + 2] = f * arr[i + 2];
      v[i + 3] = arr[i + 3];
    }
    tracks.push(new THREE.QuaternionKeyframeTrack(`${node.name}.quaternion`, times, v));
  }
  for (const [bone, q] of Object.entries(fill)) {
    if (data.bones[bone] || (keep && !keep.has(bone))) continue;
    const node = h.getNormalizedBoneNode(bone);
    if (node) tracks.push(new THREE.QuaternionKeyframeTrack(`${node.name}.quaternion`, [0], [q.x, q.y, q.z, q.w]));
  }
  const hp = data.hips_position;
  const hips = h.getNormalizedBoneNode("hips");
  if (hp && hips && (!keep || keep.has("hips"))) {
    const r = hips.position;
    const v = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      v[i * 3] = r.x + f * hp[i * 3];
      v[i * 3 + 1] = r.y + hp[i * 3 + 1];
      v[i * 3 + 2] = r.z + f * hp[i * 3 + 2];
    }
    tracks.push(new THREE.VectorKeyframeTrack(`${hips.name}.position`, times, v));
  }
  const duration = data.loop ? n / data.fps : (n - 1) / data.fps;
  return new THREE.AnimationClip(name, duration, tracks);
}

// A clip sampled on demand. Every layer samples its sources directly and the character
// inertializes between them (no mixer, no crossfades). sample(t, out) writes flipped
// quaternions into out[bone]; hipsAt(t, v) gives the hips position (rest + offset).
// `fill` supplies constant bones the clip doesn't drive (relaxed fingers); `meta` is the
// pack entry (kind, energy, rest, prep_end, stroke, ...), kept on the sampler.
export class ClipSampler {
  constructor(vrm, data, { bones, fill, name, meta } = {}) {
    const f = flipOf(vrm);
    Object.assign(this, meta ?? {});
    this.name = name ?? meta?.name;
    this.fps = data.fps;
    this.frames = data.frames ?? Math.round(data.duration * data.fps) + 1;
    this.loop = !!data.loop;
    this.duration = this.loop ? this.frames / this.fps : (this.frames - 1) / this.fps;
    this.bones = {};
    for (const [b, a] of Object.entries(data.bones)) {
      if (bones && !bones.has(b)) continue;
      const v = new Float32Array(a.length);
      for (let i = 0; i < a.length; i += 4) (v[i] = f * a[i]), (v[i + 1] = a[i + 1]), (v[i + 2] = f * a[i + 2]), (v[i + 3] = a[i + 3]);
      this.bones[b] = v;
    }
    this.fill = {};
    for (const [b, q] of Object.entries(fill ?? {})) if (!this.bones[b] && (!bones || bones.has(b))) this.fill[b] = q;
    const hp = data.hips_position, hips = vrm.humanoid.getNormalizedBoneNode("hips");
    if (hp && hips && (!bones || bones.has("hips"))) {
      const r = hips.position, v = new Float32Array(this.frames * 3);
      for (let i = 0; i < this.frames; i++) (v[i * 3] = r.x + f * hp[i * 3]), (v[i * 3 + 1] = r.y + hp[i * 3 + 1]), (v[i * 3 + 2] = r.z + f * hp[i * 3 + 2]);
      this.hips = v;
    }
    this._a = new THREE.Quaternion();
    this._b = new THREE.Quaternion();
  }

  // A procedural THREE.AnimationClip (clips.js) as a sampler, resampled from its keyframes.
  static fromAnimationClip(vrm, clip, { loop, name, fill, fps = 30 }) {
    const h = vrm.humanoid, byNode = {};
    for (const b of Object.keys(h.normalizedHumanBones ?? h.humanBones)) {
      const n = h.getNormalizedBoneNode(b);
      if (n) byNode[n.name] = b;
    }
    const n = Math.max(2, Math.round(clip.duration * fps) + (loop ? 0 : 1));
    const data = { fps, frames: n, loop, bones: {} };
    for (const tr of clip.tracks) {
      const [node, prop] = tr.name.split(".");
      const bone = byNode[node];
      if (!bone) continue;
      const it = tr.createInterpolant();
      const out = [];
      for (let i = 0; i < n; i++) out.push(...it.evaluate(Math.min(clip.duration, i / fps)));
      if (prop === "quaternion") data.bones[bone] = out;
      else if (prop === "position" && bone === "hips") {
        const r = h.getNormalizedBoneNode("hips").position, f = flipOf(vrm);
        data.hips_position = out.map((x, i) => (i % 3 === 1 ? x - r.y : f * (x - (i % 3 ? r.z : r.x))));
      }
    }
    // The procedural pack is already in the rig's space: undo the flip the constructor applies.
    const f = flipOf(vrm);
    for (const a of Object.values(data.bones)) for (let i = 0; i < a.length; i += 4) (a[i] *= f), (a[i + 2] *= f);
    return new ClipSampler(vrm, data, { name, fill });
  }

  #frame(t) {
    let x = t * this.fps;
    const n = this.frames;
    if (this.loop) x = ((x % n) + n) % n;
    else x = Math.min(Math.max(x, 0), n - 1);
    const i0 = Math.floor(x);
    return [i0, this.loop ? (i0 + 1) % n : Math.min(i0 + 1, n - 1), x - i0];
  }

  sample(t, out) {
    const [i0, i1, u] = this.#frame(t);
    for (const b in this.bones) {
      const v = this.bones[b];
      const q = out[b] ?? (out[b] = new THREE.Quaternion());
      this._a.fromArray(v, i0 * 4);
      this._b.fromArray(v, i1 * 4);
      q.slerpQuaternions(this._a, this._b, u);
    }
    for (const b in this.fill) (out[b] ?? (out[b] = new THREE.Quaternion())).copy(this.fill[b]);
    return out;
  }

  hipsAt(t, v) {
    if (!this.hips) return false;
    const [i0, i1, u] = this.#frame(t);
    const h = this.hips;
    v.set(h[i0 * 3] + (h[i1 * 3] - h[i0 * 3]) * u, h[i0 * 3 + 1] + (h[i1 * 3 + 1] - h[i0 * 3 + 1]) * u, h[i0 * 3 + 2] + (h[i1 * 3 + 2] - h[i0 * 3 + 2]) * u);
    return true;
  }
}

// ---- talk layer --------------------------------------------------------------
// The upper body while Annie speaks, over the base clip (legs and hips stay on the base).
// Sources, by priority: a per-take track time-locked to the audio (recorded-live replay);
// else a talk-rest loop with gesture phrases on top, timed by the GestureScheduler. Without
// phrases in the pack the loops themselves carry the gestures and energy picks them (the
// pre-phrase behaviour). update() reports when the shown source switches; the character
// inertializes the switch, so loops, phrases and on/off never crossfade. Per-bone weights
// keep the base breathing and gaze readable on the torso and head.
const TALK_BONE_W = { spine: 0.55, chest: 0.65, upperChest: 0.75, neck: 0.35, head: 0.3 };
const ENERGY_BUCKET = { gentle: "calm", moderate: "animated", high: "excited" };

export class TalkLayer {
  constructor(vrm, pack, rand = Math.random, phrases = []) {
    this.vrm = vrm;
    this.rand = rand;
    this.loops = { calm: [], animated: [], excited: [] };
    for (const c of Object.values(pack?.clips ?? {})) {
      if (c.layer !== "talk") continue;
      (this.loops[c.energy] ?? this.loops.animated).push(new ClipSampler(vrm, c.data, { meta: c }));
    }
    this.sched = phrases.length ? new GestureScheduler(phrases, rand) : null;
    this.names = new Set();
    for (const s of [...Object.values(this.loops).flat(), ...phrases]) for (const b of Object.keys(s.bones)) this.names.add(b);
    this.rest0 = new Map(phrases.map((p) => [p, p.sample(0, {})])); // amplitude scales motion away from frame 0
    this.w = 0; // smoothed on/off, for the procedural head sway
    this.active = false;
    this.cur = null; // talk-rest loop {s, t0, bucket}
    this.gest = null; // {phrase, t, amp} from the scheduler
    this.turn = null; // per-take track turn now playing
    this.track = null;
    this.A = {};
    this.B = {};
    this.C = {};
  }

  get bones() {
    return [...this.names];
  }

  get phrase() {
    return this.gest?.phrase ?? null;
  }

  get strokes() {
    return this.sched?.strokes ?? [];
  }

  hear(db, now) {
    this.sched?.hear(db, now);
  }

  transcript(text, now) {
    this.sched?.transcript(text, now);
  }

  setTrack(pack, audioStart) {
    this.track = {
      audioStart,
      turns: Object.values(pack.clips)
        .filter((c) => c.layer === "talk" && typeof c.offset === "number")
        .map((c) => ({ offset: c.offset, s: new ClipSampler(this.vrm, c.data) }))
        .sort((a, b) => a.offset - b.offset),
    };
    for (const t of this.track.turns) for (const b of Object.keys(t.s.bones)) this.names.add(b);
  }

  #turnAt(now) {
    const tr = this.track;
    if (!tr) return null;
    const x = now - tr.audioStart;
    return tr.turns.find((t) => x >= t.offset && x <= t.offset + t.s.duration) ?? null;
  }

  #pick(bucket, now, first) {
    const pool = this.loops[bucket].length ? this.loops[bucket] : Object.values(this.loops).flat();
    if (!pool.length) return null;
    const others = pool.filter((s) => s !== this.cur?.s);
    const src = others.length ? others : pool;
    const s = src[Math.floor(this.rand() * src.length)];
    return { s, t0: first ? now - this.rand() * s.duration * 0.5 : now, bucket };
  }

  // active: she is speaking (held over short gaps by the caller); suppress: a semantic clip
  // owns the body. Returns true when the shown source switched this frame.
  update(now, dt, { active, energy, suppress }) {
    const on = active && !suppress;
    this.w += ((on ? 1 : 0) - this.w) * (1 - Math.exp(-dt / 0.15));
    let changed = on !== this.active;
    this.active = on;
    const turn = on ? this.#turnAt(now) : null;
    if (turn !== this.turn) (changed = true), (this.turn = turn);
    if (!on || turn) {
      this.sched?.cancel(now);
      this.gest = null;
      if (!on) this.cur = null;
      return changed;
    }
    // Talk-rest loop by energy, switched at its end or on an energy change (deferred while a
    // phrase plays: the phrase hands back to the posture it started from).
    const bucket = ENERGY_BUCKET[energy] ?? "animated";
    if (!this.cur || now - this.cur.t0 >= this.cur.s.duration || (bucket !== this.cur.bucket && !this.gest)) {
      this.cur = this.#pick(bucket, now, !this.cur);
      changed = true;
    }
    if (this.sched && this.cur) {
      const g = this.sched.update(now, dt, { allowed: true, energy, rest: this.cur.s.rest });
      if ((g?.phrase ?? null) !== this.phrase) changed = true;
      this.gest = g;
    }
    return changed;
  }

  // Blend the shown upper-body pose at (now - back) into out (bone -> quaternion).
  apply(now, out, back = 0) {
    if (!this.active) return;
    const src = {};
    const put = (s, t, cache) => {
      s.sample(t, cache);
      for (const b in s.bones) src[b] = cache[b];
    };
    if (this.turn) put(this.turn.s, now - back - this.track.audioStart - this.turn.offset, this.A);
    else if (this.cur) {
      put(this.cur.s, now - back - this.cur.t0, this.A);
      const g = this.gest;
      if (g) {
        put(g.phrase, Math.max(0, g.t - back * (g.rate ?? 1)), this.B);
        if (g.amp !== 1) {
          const r0 = this.rest0.get(g.phrase);
          for (const b in g.phrase.bones) src[b] = (this.C[b] ??= new THREE.Quaternion()).copy(r0[b]).slerp(this.B[b], g.amp);
        }
      }
    } else return;
    for (const b in src) {
      const q = out[b];
      if (!q) continue;
      const w = TALK_BONE_W[b] ?? 1;
      if (w >= 1) q.copy(src[b]);
      else q.slerp(src[b], w);
    }
  }
}

// Relaxed hand for clips that don't drive fingers (VRM 1.0 space, degrees of curl).
// Replaced by the mocap pack's `hands` pose when it is installed.
export function defaultHands() {
  const out = {};
  const e = new THREE.Euler();
  const curl = { Proximal: 18, Intermediate: 28, Distal: 16 };
  for (const [s, sg] of [["left", -1], ["right", 1]]) {
    for (const f of ["Index", "Middle", "Ring", "Little"]) {
      const spread = { Index: 1, Middle: 1.1, Ring: 1.25, Little: 1.4 }[f];
      for (const [p, a] of Object.entries(curl)) out[`${s}${f}${p}`] = [0, 0, sg * a * spread * (Math.PI / 180)];
    }
    out[`${s}ThumbMetacarpal`] = [0, -sg * 0.25, 0];
    out[`${s}ThumbProximal`] = [0, -sg * 0.2, 0];
    out[`${s}ThumbDistal`] = [0, -sg * 0.15, 0];
  }
  const q = new THREE.Quaternion();
  for (const [b, r] of Object.entries(out)) out[b] = q.setFromEuler(e.set(r[0], r[1], r[2])).toArray();
  return out;
}
