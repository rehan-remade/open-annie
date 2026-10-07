import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { buildClips, CLIPS, IDLE_POOL } from "./clips.js";
import { fetchPack, poseFromData, ClipSampler, TalkLayer, defaultHands } from "./motion.js";
import { Inertializer, Pose } from "./inertia.js";
import { CollisionGuard } from "./body-collide.js";
import { stepSprings } from "./cloth.js";
import { FaceDirector, EMOTION_PRESETS } from "./faces.js";
import { VOWELS } from "./lipsync.js";
import { createMouth } from "./mouth.js";
import { clamp, mulberry32 } from "./bus.js";

const RATE = { gentle: 0.9, moderate: 1.0, high: 1.15 };
const D2R = Math.PI / 180;
const PROC_BONES = ["chest", "upperChest", "neck", "head", "leftShoulder", "rightShoulder"];
const FIDGET_GAP = [6, 12]; // s of calm idling between fidgets

// Head gestures ride on top of whatever clip is playing (additive, head/neck only).
// bnod / btilt are the small listening backchannels; s mirrors the tilt.
const GESTURES = {
  nod: { d: 1.0, f: (t) => [9 * Math.sin(t * Math.PI * 2 * 2) * Math.sin((t * Math.PI) / 1.0), 0, 0] },
  shake: { d: 1.1, f: (t) => [0, 11 * Math.sin(t * Math.PI * 2 * 2.2) * Math.sin((t * Math.PI) / 1.1), 0] },
  tilt: { d: 2.2, f: (t) => [4 * Math.sin((t * Math.PI) / 2.2), 0, 9 * Math.sin((t * Math.PI) / 2.2)] },
  duck: { d: 1.6, f: (t) => [10 * Math.sin((t * Math.PI) / 1.6), 0, -5 * Math.sin((t * Math.PI) / 1.6)] },
  bnod: { d: 0.75, f: (t) => [4.5 * Math.sin((t * Math.PI) / 0.75) ** 2, 0, 0] },
  btilt: { d: 2.6, f: (t, s) => [1.5 * Math.sin((t * Math.PI) / 2.6), 1.2 * s * Math.sin((t * Math.PI) / 2.6), 5.5 * s * Math.sin((t * Math.PI) / 2.6)] },
};

function removeOutfitParts(vrm, { materials = [], springs = null }) {
  const doomed = [];
  vrm.scene.traverse((o) => {
    if (o.isMesh && [].concat(o.material).some((m) => materials.some((s) => m.name.includes(s)))) doomed.push(o);
  });
  for (const o of doomed) o.parent.remove(o);
  const sb = vrm.springBoneManager;
  if (sb && springs) {
    const re = new RegExp(springs);
    for (const j of [...sb.joints]) if (re.test(j.bone.name)) sb.deleteJoint(j);
  }
}

export class Character {
  // motion: {packs, talk} base URLs of JSON motion packs, highest priority first (default:
  // whatever assets/clips/index.json, next to the avatars, lists). Clips no pack provides
  // come from the procedural annie-core pack.
  // hide: {materials: [name substrings], springs: "bone-name regex"} removes outfit parts
  // (e.g. a jacket) before anything is built from the mesh, so the collision guard, the
  // shadows and the cloth physics only ever see what is actually shown.
  static async load({ url, scene, camera, seed = 7, onProgress, motion = {}, hide = null }) {
    const clipsBase = new URL("../../clips/", url);
    // assets/clips/index.json lists the installed packs: {body: [ids, priority order], talk: id}.
    const packs = fetch(new URL("index.json", clipsBase))
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}))
      .then((idx) => {
        const at = (id) => new URL(`${id}/`, clipsBase).href;
        // idx.picks {clip: packId} chooses a source per clip (A/B winners); ignored under an override.
        const picks = motion.packs ? {} : idx.picks ?? {};
        const ids = [...new Set([...Object.values(picks), ...(idx.body ?? [])])];
        const body = motion.packs ?? ids.map(at);
        const talk = motion.talk ?? (idx.talk ? at(idx.talk) : null);
        return Promise.all([
          Promise.all(body.map((u) => fetchPack(u).catch(() => null))),
          talk ? fetchPack(talk).catch(() => null) : null,
          picks,
        ]);
      });
    const loader = new GLTFLoader();
    loader.register((p) => new VRMLoaderPlugin(p));
    const gltf = await loader.loadAsync(url, onProgress);
    const vrm = gltf.userData.vrm;
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons?.(gltf.scene);
    VRMUtils.rotateVRM0(vrm);
    vrm.scene.traverse((o) => {
      if (o.isMesh) o.frustumCulled = false;
    });
    if (hide) removeOutfitParts(vrm, hide);
    scene.add(vrm.scene);
    const [list, talk, picks] = await packs;
    // Merge: picked clips first, then the first pack that has a clip wins; its relaxed-hand pose is kept too.
    const found = list.filter(Boolean);
    const pack = found.length ? { clips: {}, hands: found.find((p) => p.hands)?.hands, ids: found.map((p) => p.id) } : null;
    for (const [k, id] of Object.entries(picks ?? {})) {
      const c = found.find((p) => p.id === id)?.clips[k];
      if (c) pack.clips[k] = c;
    }
    for (const p of found) for (const [k, c] of Object.entries(p.clips)) pack.clips[k] ??= c;
    return new Character(vrm, camera, seed, { pack, talk });
  }

  constructor(vrm, camera, seed, { pack = null, talk = null } = {}) {
    this.vrm = vrm;
    this.camera = camera;
    this.rand = mulberry32(seed);
    // Built first, while the rig is still in its rest pose.
    this.guard = new CollisionGuard(vrm);
    this.face = new FaceDirector({ seed });
    this.mouth = Object.fromEntries(VOWELS.map((v) => [v, 0]));
    this.lips = createMouth(vrm); // mouth rig: stage feeds it features, update() applies it
    this.speaking = false;
    this.energy = "moderate";

    // three-vrm attenuates lipsync presets while an emotion with overrideMouth is
    // active, the opposite of what we want: the app gates the mouth itself.
    const em = vrm.expressionManager;
    // VRM 0.x has no surprised preset; VRoid exports it as a custom "Surprised".
    const alias = (p) => (em?.getExpression(p) ? p : em?.getExpression(p[0].toUpperCase() + p.slice(1)) ? p[0].toUpperCase() + p.slice(1) : null);
    this.names = Object.fromEntries([...EMOTION_PRESETS, ...VOWELS, "blink"].map((p) => [p, alias(p)]));
    for (const p of EMOTION_PRESETS) {
      const e = this.names[p] && em.getExpression(this.names[p]);
      if (e) e.overrideMouth = "none";
    }
    this.has = Object.fromEntries(Object.entries(this.names).map(([k, v]) => [k, !!v]));

    // Body clips, sampled directly: the pack when installed, else procedural annie-core.
    // Relaxed fingers fill any clip that doesn't drive them (no flat hands).
    const hands = poseFromData(vrm, pack?.hands ?? defaultHands());
    this.clips = {};
    this.meta = {};
    const phrases = [];
    for (const src of [pack, talk]) {
      for (const [k, c] of Object.entries(src?.clips ?? {})) {
        if (c.layer === "gesture" && !phrases.some((p) => p.name === k)) phrases.push(new ClipSampler(vrm, c.data, { name: k, meta: c }));
        if (src !== pack || c.layer === "talk" || c.layer === "gesture") continue; // talk-layer clips belong to the TalkLayer
        this.clips[k] = new ClipSampler(vrm, c.data, { name: k, fill: hands });
        this.meta[k] = { loop: c.loop, duration: this.clips[k].duration, kind: c.kind ?? (c.loop ? "idle" : "gesture") };
      }
    }
    for (const [k, c] of Object.entries(buildClips(vrm))) {
      if (this.clips[k]) continue; // procedural clips cover anything the pack lacks
      this.clips[k] = ClipSampler.fromAnimationClip(vrm, c, { loop: !!CLIPS[k].loop, name: k, fill: hands });
      this.meta[k] = { loop: !!CLIPS[k].loop, duration: CLIPS[k].duration, kind: CLIPS[k].loop ? "idle" : CLIPS[k].kind ?? "gesture" };
    }
    const pool = (kind) => Object.keys(this.meta).filter((k) => this.meta[k].kind === kind && (!pack || pack.clips[k]));
    this.idlePool = pack && pool("idle").length ? pool("idle") : IDLE_POOL;
    this.listenPool = pool("listen");
    this.fidgets = pool("fidget");
    this.listening = false;
    this.talk = talk || phrases.length ? new TalkLayer(vrm, talk, this.rand, phrases) : null;
    this.lastVoiced = -Infinity;

    // One pose over every bone any source drives; the inertializer works on it.
    const h = vrm.humanoid;
    const names = new Set(PROC_BONES);
    for (const s of Object.values(this.clips)) for (const b of [...Object.keys(s.bones), ...Object.keys(s.fill)]) names.add(b);
    for (const b of this.talk?.bones ?? []) names.add(b);
    this.bones = [...names].filter((b) => h.getNormalizedBoneNode(b));
    this.nodes = this.bones.map((b) => h.getNormalizedBoneNode(b));
    this.map = Object.fromEntries(this.bones.map((b) => [b, new THREE.Quaternion()]));
    this.hipsNode = h.getNormalizedBoneNode("hips");
    this.hipsRest = this.hipsNode.position.clone();
    this.pose = new Pose(this.bones.length);
    this.prev = new Pose(this.bones.length);
    this.inert = new Inertializer(this.bones.length);
    this.pending = false;

    this.current = null; // base source {name, s, t0, rate, idle, kind, endsAt}
    this.idleName = null;
    this.idleSince = 0;
    this.calmSince = 0;
    this.fidgetGap = FIDGET_GAP[0] + (FIDGET_GAP[1] - FIDGET_GAP[0]) * this.rand();
    this.time = 0;
    this.gestures = [];
    this.user = { speaking: false, level: 0, lastVoiced: -Infinity };
    this.bc = null;
    this.nextBlink = 1.5;
    this.blinkT = -1;
    this.gaze = { target: new THREE.Object3D(), until: 0, off: new THREE.Vector3() };
    vrm.lookAt && (vrm.lookAt.target = this.gaze.target);
    this.#startIdle(0);
  }

  get head() {
    return this.vrm.humanoid.getNormalizedBoneNode("head");
  }

  // One predicate for "which loop pool" — the pool-change check in update() uses it too.
  // (They used to disagree while she spoke over a still-streaming user transcript, which
  // restarted a loop every frame and made the head pop.)
  get #wantsListen() {
    return this.listening && !this.speaking && this.listenPool.length > 0;
  }

  #startIdle(now) {
    const src = this.#wantsListen ? this.listenPool : this.idlePool;
    const pool = src.filter((n) => n !== this.idleName);
    const name = (pool.length ? pool : src)[Math.floor(this.rand() * (pool.length || src.length))];
    this.idleName = name;
    this.idleListening = src === this.listenPool;
    this.poolWantSince = null;
    this.idleSince = now;
    this.current = { name, s: this.clips[name], t0: now, rate: 1, idle: true, kind: this.meta[name].kind, endsAt: Infinity };
  }

  playClip(name, { energy = this.energy } = {}) {
    const s = this.clips[name];
    if (!s || this.meta[name].loop) return false;
    if (this.current && !this.current.idle && this.current.name === name) return false;
    const rate = RATE[energy] ?? 1;
    // Starts on the next update (t0 then); the switch is inertialized there.
    this.current = { name, s, t0: null, rate, idle: false, kind: this.meta[name].kind, endsAt: Infinity };
    this.pending = true;
    return true;
  }

  gesture(name, { side = 1 } = {}) {
    if (!GESTURES[name]) return false;
    this.gestures = this.gestures.filter((g) => g.name !== name);
    this.gestures.push({ name, t0: this.time, s: side });
    return true;
  }

  setFace(name, intensity = 1) {
    return this.face.set(name, intensity, this.time);
  }

  setMouth(weights, speaking) {
    Object.assign(this.mouth, weights);
    this.speaking = speaking;
  }

  // Stage feed, every frame before update(): Annie's lipsync features (her audio level drives
  // gesture timing) and the user's voice activity (listening backchannels).
  hear(now, f, userSpeaking = false, userLevel = 0) {
    this.talk?.hear(f && f.amp > 0 ? f.db : null, now);
    const u = this.user;
    u.speaking = userSpeaking;
    u.level = userLevel;
    if (userSpeaking) u.lastVoiced = now;
  }

  // Assistant transcript so far (runs ahead of the audio): sentence ends and emphasis.
  transcript(text, now) {
    this.talk?.transcript(text, now);
  }

  // Recorded-live replay: a per-take talk track (docs/contracts.md) time-locked to the audio.
  // audioStart is the track's offset on the session clock; audioFile guards against a
  // track generated for different audio.
  async setTalkTrack(packOrUrl, audioStart, audioFile) {
    if (!this.talk) return false;
    const pack = typeof packOrUrl === "string" ? await fetchPack(packOrUrl) : packOrUrl;
    if (audioFile && pack.audio?.file && pack.audio.file !== audioFile) return false;
    this.talk.setTrack(pack, audioStart);
    return true;
  }

  bargeIn() {
    this.face.set("surprised", 1, this.time);
    this.playClip("surprised_recoil", { energy: "high" });
  }

  update(now, dt) {
    this.time = now;
    let switched = this.#advanceBase(now) || this.pending;
    this.pending = false;
    // Talk layer: upper-body co-speech gesture while she speaks (held over short word gaps);
    // a semantic clip owns the body until it ends.
    if (this.speaking) this.lastVoiced = now;
    const cur = this.current;
    const sem = !cur.idle && cur.kind !== "fidget";
    if (this.talk?.update(now, dt, { active: now - this.lastVoiced < 0.5, energy: this.energy, suppress: sem })) switched = true;

    // Compose the sources; on a switch, capture the offset from what was on screen.
    this.#compose(now, this.pose, 0);
    if (switched) {
      this.#compose(now, this.prev, dt);
      this.inert.transition(this.prev, this.pose, dt);
    }
    this.inert.apply(this.pose, dt);
    for (let i = 0; i < this.nodes.length; i++) this.nodes[i].quaternion.copy(this.pose.q[i]);
    this.hipsNode.position.copy(this.pose.hips);
    this.#backchannel(now);
    this.#procedural(now);
    this.guard.update(dt);

    // Expression layer, then the viseme layer, which owns the mouth while speaking.
    const em = this.vrm.expressionManager;
    this.face.speaking = this.speaking;
    const faceW = this.face.update(now, dt);
    if (em) {
      for (const p of EMOTION_PRESETS) if (this.has[p]) em.setValue(this.names[p], faceW[p]);
      this.lips.apply(em, this.mouth, faceW); // vowels/raw mouth morphs + talking-face split
      if (this.has.blink) em.setValue("blink", this.#blink(now, faceW));
    }
    stepSprings(this.vrm, dt, 4);
  }

  // Idle / listen pool changes, a finished semantic clip or fidget, and fidget triggers.
  // Returns true when the base source switched.
  #advanceBase(now) {
    const cur = this.current;
    if (cur.t0 == null) {
      cur.t0 = now;
      cur.endsAt = now + cur.s.duration / cur.rate;
    }
    // Hysteresis: a pool change must persist 0.3 s, so a flicker in speaking/listening
    // doesn't restart the loop.
    const want = this.#wantsListen;
    if (want !== this.idleListening) this.poolWantSince ??= now;
    else this.poolWantSince = null;
    const calm = cur.idle && !this.idleListening && !this.speaking && now - this.lastVoiced > 1.5 && now - this.user.lastVoiced > 1.5;
    if (!calm && cur.kind !== "fidget") this.calmSince = now;
    if (cur.idle) {
      const poolChanged = this.poolWantSince != null && now - this.poolWantSince > 0.3;
      if (poolChanged || now - this.idleSince > this.meta[cur.name].duration * 2) return this.#startIdle(now), true;
      if (calm && this.fidgets.length && now - this.calmSince > this.fidgetGap) {
        const name = this.fidgets[Math.floor(this.rand() * this.fidgets.length)];
        const s = this.clips[name];
        this.current = { name, s, t0: now, rate: 1, idle: false, kind: "fidget", endsAt: now + s.duration, resume: cur };
        return true;
      }
      return false;
    }
    const busy = this.#wantsListen || this.speaking;
    if (now < cur.endsAt && !(cur.kind === "fidget" && busy)) return false; // a fidget gives way at once to conversation
    if (cur.resume && !busy) this.current = cur.resume; // a fidget hands back to its idle, phase intact
    else this.#startIdle(now);
    this.calmSince = now;
    this.fidgetGap = FIDGET_GAP[0] + (FIDGET_GAP[1] - FIDGET_GAP[0]) * this.rand();
    return true;
  }

  // The animation pose at (now - back): base clip, then the talk layer over the upper body.
  #compose(now, pose, back) {
    const m = this.map;
    for (const b in m) m[b].identity();
    const c = this.current;
    const t = (now - back - c.t0) * c.rate;
    c.s.sample(t, m);
    if (!c.s.hipsAt(t, pose.hips)) pose.hips.copy(this.hipsRest);
    this.talk?.apply(now, m, back);
    for (let i = 0; i < this.bones.length; i++) pose.q[i].copy(m[this.bones[i]]);
  }

  // While the user talks: a small nod at their pauses, and now and then a head tilt.
  // A run of speech spans short word gaps; a pause is 0.22 s of quiet after a run of ~1 s.
  #backchannel(now) {
    const u = this.user;
    if (this.speaking || now - u.lastVoiced > 1.5) {
      this.bc = null;
      return;
    }
    const bc = (this.bc ??= { runStart: null, loud: -Infinity, lastNod: -Infinity, tiltAt: now + 1.5 + 2 * this.rand() });
    if (u.speaking && u.level > 0.12) {
      bc.loud = now;
      bc.runStart ??= now;
    } else if (bc.runStart != null && now - bc.loud > 0.22) {
      if (bc.loud - bc.runStart > 0.9 && now - bc.lastNod > 2.2 && this.rand() < 0.8) this.gesture("bnod"), (bc.lastNod = now);
      bc.runStart = null;
    }
    if (now > bc.tiltAt && u.speaking) {
      this.gesture("btilt", { side: this.rand() < 0.5 ? -1 : 1 });
      bc.tiltAt = now + 5 + 4 * this.rand();
    }
  }

  #procedural(now) {
    const h = this.vrm.humanoid;
    const q = new THREE.Quaternion();
    const e = new THREE.Euler();
    const f = this.vrm.meta?.metaVersion === "0" ? -1 : 1;
    const apply = (bone, x, y, z) => {
      const n = h.getNormalizedBoneNode(bone);
      if (!n) return;
      q.setFromEuler(e.set(f * x * D2R, y * D2R, f * z * D2R));
      n.quaternion.multiply(q);
    };
    // Breathing on a 3.2 s cycle, sway on incommensurate periods so it never phase-locks.
    const br = Math.sin((now * Math.PI * 2) / 3.2);
    apply("chest", -0.9 * br, 0, 0);
    apply("upperChest", -0.6 * br, 0, 0);
    apply("leftShoulder", 0, 0, 0.8 * br);
    apply("rightShoulder", 0, 0, -0.8 * br);
    const sway = (p) => Math.sin((now * Math.PI * 2) / p);
    apply("neck", 0.8 * sway(6.5), 1.2 * sway(15.5), 0.8 * sway(5.5));
    const hs = 1 - 0.6 * (this.talk?.w ?? 0); // the talk layer already moves the head
    if (this.speaking) apply("head", 1.4 * hs * sway(1.7), 1.0 * hs * sway(2.3), 0.8 * hs * sway(3.5));

    this.gestures = this.gestures.filter((g) => now - g.t0 < GESTURES[g.name].d);
    for (const g of this.gestures) {
      const [x, y, z] = GESTURES[g.name].f(now - g.t0, g.s);
      apply("head", x * 0.7, y * 0.7, z * 0.7);
      apply("neck", x * 0.3, y * 0.3, z * 0.3);
    }

    // Gaze: mostly camera contact, with short glances away.
    const gz = this.gaze;
    if (now >= gz.until) {
      const contact = this.rand() < (this.speaking ? 0.65 : 0.55);
      gz.off.set(contact ? 0 : (this.rand() - 0.5) * 0.6, contact ? 0 : (this.rand() - 0.3) * 0.3, 0);
      gz.until = now + (contact ? 1.5 + 3 * this.rand() : 0.3 + 0.9 * this.rand());
    }
    const cam = this.camera.position;
    gz.target.position.lerp(new THREE.Vector3(cam.x + gz.off.x, cam.y + gz.off.y, cam.z), 0.2);
  }

  // Blink every 2 to 6 s (3 to 8 speaking), 15% doubles, 50/40/100 ms close/hold/open.
  #blink(now, faceW) {
    if ((faceW.happy ?? 0) > 0.5 || (faceW.relaxed ?? 0) > 0.6) return 0;
    if (this.blinkT < 0 && now >= this.nextBlink) this.blinkT = now;
    if (this.blinkT < 0) return 0;
    const t = now - this.blinkT;
    let v = t < 0.05 ? t / 0.05 : t < 0.09 ? 1 : t < 0.19 ? 1 - (t - 0.09) / 0.1 : 0;
    if (t >= 0.19) {
      this.blinkT = -1;
      const dbl = this.rand() < 0.15;
      this.nextBlink = now + (dbl ? 0.12 : this.speaking ? 3 + 5 * this.rand() : 2 + 4 * this.rand());
    }
    return clamp(v);
  }
}
