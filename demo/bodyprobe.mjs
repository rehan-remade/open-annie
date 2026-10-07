// Body probe: frame-steps the stage and measures, per frame,
//  - how deep hand / finger / forearm / elbow samples sink into the body: the skinned mesh
//    (skin, top, cardigan, shorts, hair) is CPU-skinned every frame and the torso, hips,
//    thighs and head are rebuilt as polar height maps from it (core/src/body-collide.js);
//  - joint pops: per-bone angular acceleration spikes (deg/frame^2 at 30 fps);
//  - foot slide while a foot is planted;
//  - gesture strokes vs audio onsets (replay only, when the character logs strokes).
//   node demo/bodyprobe.mjs [--mode replay|clips|phrases|both|all] [--avatar id] [--clips a,b] [--label name] [--out dir]
//                           [--query "&k=v"] [--shots 33,36.5] [--from 0 --to 87]
// Writes <out>/probe-<label>.json (per-frame rows) and prints a summary.
import { launch } from "./browser.mjs";
import { mkdirSync, writeFileSync, readFileSync, existsSync } from "node:fs";

const args = process.argv.slice(2);
const opt = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
const MODE = opt("--mode", "replay");
const LABEL = opt("--label", "probe");
const OUT = opt("--out", "/tmp/bodyprobe");
const AVATAR = opt("--avatar", null); // avatar id from assets/avatars/index.json (default: the installed default)
const EXTRA = opt("--query", "") + (AVATAR ? `&avatar=${AVATAR}` : "");
const SHOTS = opt("--shots", "").split(",").filter(Boolean).map(Number);
// A/B: serve the committed (HEAD) runtime and/or body pack from an extracted copy, and/or
// switch the collision guard off.
const HEAD_DIR = opt("--head", null);
const OLD_RUNTIME = args.includes("--old-runtime"), OLD_PACK = args.includes("--old-pack"), NOGUARD = args.includes("--noguard");
const STAGE = "http://127.0.0.1:8080/stage/";
const REPLAY = "replay=demo/live/session.json&decisions=demo/live/decisions.json";
const FPS = 30;
const CLIPS = opt("--clips", "idle_a,idle_b,idle_c,listen_a,listen_b,wave,clap,shrug,think,laugh,sad_slump,surprised_recoil,bow,point_self,excited_bounce,dance,talk_calm_1,talk_calm_2,talk_calm_3,talk_animated_1,talk_animated_2,talk_animated_3,talk_excited_1,talk_excited_2,fidget_hair,fidget_sleeve,fidget_rock,fidget_clasp,fidget_glance,fidget_stretch").split(",");
mkdirSync(OUT, { recursive: true });

// ---- in-page measurement ------------------------------------------------------
async function install() {
  const THREE = await import("three");
  // The measuring code is always the working tree's (?probe skips --old-runtime's routing).
  const { BodySurface, buildRegionMaps, REGIONS, clearances } = await import("/packages/core/src/body-collide.js?probe");
  const c = window.capture.character, vrm = c.vrm, h = vrm.humanoid;
  vrm.scene.updateMatrixWorld(true);
  const surf = new BodySurface(vrm);
  const nodes = REGIONS.map((r) => h.getNormalizedBoneNode(r));
  const arm = [];
  for (let s = 0; s < surf.count; s++) if (surf.cls[s] >= 0) arm.push(s);
  const POP = ["hips", "spine", "chest", "upperChest", "neck", "head", "leftShoulder", "rightShoulder", "leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm", "leftHand", "rightHand", "leftUpperLeg", "rightUpperLeg", "leftLowerLeg", "rightLowerLeg", "leftFoot", "rightFoot"];
  const p = new THREE.Vector3(), l = new THREE.Vector3(), n = new THREE.Vector3(), sp = new THREE.Vector3();
  const wp = (b) => {
    const nd = h.getRawBoneNode(b);
    return nd ? p.setFromMatrixPosition(nd.matrixWorld).toArray().map((x) => +x.toFixed(5)) : null;
  };
  window.__probe = {
    info: { samples: surf.count, arm: arm.length, body: surf.count - arm.length },
    measure() {
      vrm.scene.updateMatrixWorld(true);
      const pos = surf.skin();
      const maps = buildRegionMaps(surf, pos, nodes.map((nd) => nd?.matrixWorld));
      for (const m of maps) (m.c = m.sphere.c.clone().applyMatrix4(m.frame)), (m.r2 = (m.sphere.r + 0.05) ** 2);
      const cq = {};
      const depth = [[-1, -1, -1, -1], [-1, -1, -1, -1]]; // side x part, metres (negative = clear)
      const hard = [[-1, -1, -1, -1], [-1, -1, -1, -1]]; // minus the loose-cloth give (elbow end only)
      const where = [["", "", "", ""], ["", "", "", ""]];
      let n5 = 0;
      for (const s of arm) {
        p.fromArray(pos, s * 3);
        const side = surf.cls[s] >> 2, part = surf.cls[s] & 3, gw = surf.giveW[s];
        let d = -Infinity, reg = "";
        for (const m of maps) {
          l.copy(p).applyMatrix4(m.inv);
          if (l.distanceTo(m.sphere.c) > m.sphere.r + 0.02) continue;
          let q = m.map.query(l.x, l.y, l.z, n);
          // Inside, the true depth is the distance to the nearest envelope surface (the radial
          // depth is an upper bound, so only candidates that could set a new maximum are refined).
          if (q > 0.005 && (q > depth[side][part] || q > hard[side][part])) q = Math.min(q, m.map.nearest(l.x, l.y, l.z, q + 0.01, sp));
          if (q > d) (d = q), (reg = m.name);
        }
        // Beyond what loose cloth gives (the elbow end of the arm may rest pressed into it).
        const dh = gw > 0 && d > -0.03 ? d - gw * clearances(maps, p, n, cq).give : d;
        if (d > depth[side][part]) (depth[side][part] = d), (where[side][part] = reg);
        if (dh > hard[side][part]) hard[side][part] = dh;
        if (d > 0.005) n5++;
      }
      const q = {};
      for (const b of POP) {
        const nd = h.getNormalizedBoneNode(b);
        if (nd) q[b] = nd.quaternion.toArray().map((x) => +x.toFixed(6));
      }
      const g = c.guard?.stats;
      return {
        clip: c.current?.name ?? null, talk: +(c.talk?.w ?? 0).toFixed(2), phrase: c.talk?.phrase?.name ?? null, spk: c.speaking ? 1 : 0,
        depth: depth.map((r) => r.map((x) => +x.toFixed(4))), hard: hard.map((r) => r.map((x) => +x.toFixed(4))), where, n5, q,
        hand: [wp("leftHand"), wp("rightHand")], foot: [wp("leftFoot"), wp("rightFoot")], toes: [wp("leftToes"), wp("rightToes")],
        guard: g ? { ...g } : undefined,
      };
    },
  };
  return window.__probe.info;
}

// Annie's recorded track -> onsets, offline and non-causal (the reference the live scheduler
// is measured against): 60 fps levels as lipsync.js computes them; an onset where the level
// has risen 6 dB over the last 160 ms while voiced (> -40 dBFS), 100 ms apart, placed at the
// frame the rise crosses the threshold. Strong: its peak within 150 ms is above the median
// peak of its utterance (the stressed syllables).
async function audioOnsets() {
  const { analyseSamples } = await import("/packages/core/src/lipsync.js");
  const a = window.capture.audio.at(-1);
  const buf = await fetch(new URL(`/${a.file}`, location.href)).then((r) => r.arrayBuffer());
  const dec = await new OfflineAudioContext(1, 48000, 48000).decodeAudioData(buf);
  const { fps, frames } = analyseSamples(dec.getChannelData(0), dec.sampleRate, 60);
  const db = frames.map((f) => f.db + 4.3);
  const out = [];
  let last = -1;
  for (let i = 10; i < db.length; i++) {
    const mn = Math.min(...db.slice(i - 10, i));
    if (db[i] > -40 && db[i] - mn > 6 && i - last > 6) {
      last = i;
      out.push({ t: a.start + i / fps, peak: Math.max(...db.slice(i, i + 9)) });
    }
  }
  // Utterances: onsets closer than 0.4 s belong together; strong = peak above the utterance median.
  let u = [];
  const flush = () => {
    const med = [...u].sort((x, y) => x.peak - y.peak)[u.length >> 1]?.peak ?? 0;
    for (const o of u) o.strong = o.peak >= med;
    u = [];
  };
  for (const o of out) {
    if (u.length && o.t - u.at(-1).t > 0.4) flush();
    u.push(o);
  }
  flush();
  return out.map((o) => ({ t: +o.t.toFixed(3), strong: o.strong }));
}

// Stroke - nearest onset (s): p50 / p90 of |offset|, median signed offset, a 50 ms histogram,
// and the same for random times inside speech (what placement without timing would score).
function syncStats(strokes, onsets) {
  if (!strokes.length || !onsets.length) return null;
  const nearest = (t, list) => list.reduce((b, o) => (Math.abs(o.t - t) < Math.abs(b) ? o.t - t : b), Infinity) * -1;
  const summarize = (xs) => {
    const abs = xs.map(Math.abs).sort((a, b) => a - b), sorted = [...xs].sort((a, b) => a - b);
    const hist = {};
    for (const x of xs) {
      const k = Math.max(-0.3, Math.min(0.3, Math.round(x / 0.05) * 0.05)).toFixed(2);
      hist[k] = (hist[k] ?? 0) + 1;
    }
    return { n: xs.length, p50: +pct(abs, 0.5).toFixed(3), p90: +pct(abs, 0.9).toFixed(3), medianSigned: +pct(sorted, 0.5).toFixed(3), within100ms: +(abs.filter((x) => x <= 0.1).length / xs.length).toFixed(2), hist };
  };
  const strong = onsets.filter((o) => o.strong);
  // Random reference: 2000 times drawn inside the spans the strokes fell in (seeded LCG).
  let seed = 7;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  const lo = Math.min(...strokes.map((s) => s.t)), hi = Math.max(...strokes.map((s) => s.t));
  const voiced = [];
  for (let i = 0; i < 2000; i++) {
    const t = lo + rnd() * (hi - lo);
    if (onsets.some((o) => Math.abs(o.t - t) < 0.4)) voiced.push(t);
  }
  const off = (list, ts) => ts.map((t) => nearest(t, list));
  return {
    strokes: summarize(off(onsets, strokes.map((s) => s.t))),
    strokesToStrong: summarize(off(strong, strokes.map((s) => s.t))),
    randomToAll: summarize(off(onsets, voiced)),
    randomToStrong: summarize(off(strong, voiced)),
    onsets: onsets.length,
    strong: strong.length,
  };
}

async function openPage(browser, query) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on("pageerror", (e) => console.error("[pageerror]", e.message));
  page.on("console", (m) => m.type() === "error" && console.error("[page]", m.text()));
  if (OLD_RUNTIME)
    await page.route("**/packages/core/src/*.js", (r) => {
      const f = `${HEAD_DIR}/packages/core/src/${new URL(r.request().url()).pathname.split("/").pop()}`;
      return existsSync(f) ? r.fulfill({ path: f, contentType: "text/javascript" }) : r.continue();
    });
  if (OLD_PACK) await page.route("**/assets/clips/annie-blender/*", (r) => r.fulfill({ path: `${HEAD_DIR}/assets/clips/annie-blender/${new URL(r.request().url()).pathname.split("/").pop()}`, contentType: "application/json" }));
  await page.goto(`${STAGE}?capture=1&${query}${EXTRA}`);
  await page.waitForSelector("body[data-ready='1']", { timeout: 120000 });
  if (NOGUARD) await page.evaluate(() => window.capture.character.guard && (window.capture.character.guard.enabled = false));
  const info = await page.evaluate(install);
  return { page, info };
}

// ---- summaries ----------------------------------------------------------------
const pct = (a, q) => (a.length ? [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(q * a.length))] : 0);
const qmul = (a, b) => [
  a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
  a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
  a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
  a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2],
];
const qinv = (a) => [-a[0], -a[1], -a[2], a[3]];
function rotvec(q) {
  let [x, y, z, w] = q;
  if (w < 0) (x = -x), (y = -y), (z = -z), (w = -w);
  const s = Math.hypot(x, y, z);
  if (s < 1e-9) return [0, 0, 0];
  const a = 2 * Math.atan2(s, w);
  return [(x / s) * a, (y / s) * a, (z / s) * a];
}
const DEG = 180 / Math.PI;

// Per-bone angular velocity (deg/frame) and acceleration (deg/frame^2); a pop is an
// acceleration spike above POP_T. Hand positions: linear acceleration (mm/frame^2).
const POP_T = 4;
export function motionStats(rows, skip = () => false) {
  const bones = Object.keys(rows[0]?.q ?? {});
  const acc = {}, vel = {}, pops = [];
  for (const b of bones) {
    acc[b] = [];
    vel[b] = [];
    let wPrev = null;
    for (let i = 1; i < rows.length; i++) {
      const a = rows[i - 1].q[b], c = rows[i].q[b];
      if (!a || !c) continue;
      const w = rotvec(qmul(qinv(a), c));
      vel[b].push(Math.hypot(...w) * DEG);
      if (wPrev && !skip(rows[i])) {
        const d = Math.hypot(w[0] - wPrev[0], w[1] - wPrev[1], w[2] - wPrev[2]) * DEG;
        acc[b].push(d);
        if (d > POP_T) pops.push({ t: rows[i].t, bone: b, d: +d.toFixed(1), clip: rows[i].clip, phrase: rows[i].phrase });
      }
      wPrev = w;
    }
  }
  const handAcc = [];
  for (let i = 2; i < rows.length; i++) {
    if (skip(rows[i])) continue;
    for (const s of [0, 1]) {
      const a = rows[i - 2].hand[s], b = rows[i - 1].hand[s], c = rows[i].hand[s];
      handAcc.push(Math.hypot(c[0] - 2 * b[0] + a[0], c[1] - 2 * b[1] + a[1], c[2] - 2 * b[2] + a[2]) * 1000);
    }
  }
  // Spikes: a pop that stands out from its neighbourhood (a velocity step). A switch that
  // eases in with an acceleration ramp, or a fast authored stroke, is not a spike.
  let spikes = 0;
  const spikeList = [];
  for (const [b, a] of Object.entries(acc)) {
    for (let i = 3; i < a.length - 3; i++) {
      if (a[i] <= POP_T) continue;
      const before = (a[i - 1] + a[i - 2] + a[i - 3]) / 3, after = (a[i + 1] + a[i + 2] + a[i + 3]) / 3;
      if (a[i] > 2.5 * Math.max(before, after)) spikes++, spikeList.push({ i, bone: b, d: +a[i].toFixed(1) });
    }
  }
  const allAcc = Object.values(acc).flat();
  return {
    spikes,
    spikeList: spikeList.sort((x, y) => y.d - x.d).slice(0, 10),
    popFrames: new Set(pops.map((p) => p.t)).size,
    pops: pops.length,
    accP99: +pct(allAcc, 0.99).toFixed(2),
    accMax: +Math.max(0, ...allAcc).toFixed(1),
    worstBones: Object.entries(acc).map(([b, a]) => [b, +Math.max(0, ...a).toFixed(1)]).sort((x, y) => y[1] - x[1]).slice(0, 5),
    handAccP99: +pct(handAcc, 0.99).toFixed(2),
    handAccMax: +Math.max(0, ...handAcc).toFixed(1),
    popList: pops.sort((a, b) => b.d - a.d).slice(0, 12),
  };
}

// Foot slide: horizontal ankle motion while the toes are within 1 cm of their lowest.
export function footStats(rows, skip = () => false) {
  const out = {};
  for (const s of [0, 1]) {
    const minToe = Math.min(...rows.map((r) => r.toes[s][1]));
    let total = 0, max = 0;
    for (let i = 1; i < rows.length; i++) {
      if (skip(rows[i])) continue;
      const a = rows[i - 1], b = rows[i];
      if (a.toes[s][1] > minToe + 0.01 || b.toes[s][1] > minToe + 0.01) continue;
      const d = Math.hypot(b.foot[s][0] - a.foot[s][0], b.foot[s][2] - a.foot[s][2]) * 1000;
      total += d;
      max = Math.max(max, d);
    }
    out[s ? "right" : "left"] = { slideMm: +total.toFixed(1), maxMmPerFrame: +max.toFixed(2) };
  }
  return out;
}

export function penStats(rows, key = "depth") {
  const PART = ["forearm", "hand", "finger", "elbow"];
  rows = rows.map((r) => ({ ...r, depth: r[key] ?? r.depth }));
  const worst = rows.map((r) => Math.max(...r.depth.flat()));
  const by = {};
  for (const [i, p] of PART.entries()) by[p] = +(1000 * Math.max(...rows.map((r) => Math.max(r.depth[0][i], r.depth[1][i])))).toFixed(1);
  const frames = (mm) => worst.filter((d) => d > mm / 1000).length;
  return {
    frames: rows.length,
    over5mm: frames(5),
    over10mm: frames(10),
    over20mm: frames(20),
    maxMm: +(1000 * Math.max(...worst)).toFixed(1),
    p99Mm: +(1000 * pct(worst, 0.99)).toFixed(1),
    byPartMaxMm: by,
  };
}

// Worst penetration moments, merged into episodes.
function episodes(rows, mm = 5) {
  const out = [];
  let cur = null;
  const PART = ["forearm", "hand", "finger", "elbow"];
  for (const r of rows) {
    const flat = r.depth.flat();
    const d = Math.max(...flat);
    if (d > mm / 1000) {
      const k = flat.indexOf(d);
      const side = k < 4 ? "L" : "R", part = PART[k % 4], reg = r.where[k >> 2][k % 4];
      if (!cur || r.t - cur.t1 > 0.1) out.push((cur = { t0: r.t, t1: r.t, maxMm: 0 }));
      cur.t1 = r.t;
      if (d * 1000 > cur.maxMm) Object.assign(cur, { maxMm: +(d * 1000).toFixed(1), at: r.t, what: `${side} ${part} in ${reg}`, clip: r.clip, phrase: r.phrase });
    } else cur = null;
  }
  return out.sort((a, b) => b.maxMm - a.maxMm);
}

// ---- runs -----------------------------------------------------------------------
const browser = await launch();
const result = { label: LABEL, query: EXTRA, when: new Date().toISOString() };

if (MODE === "summarize") {
  const rows = JSON.parse(readFileSync(opt("--rows")));
  const dance = (r) => r.clip === "dance";
  result.replay = { pen: penStats(rows), hard: penStats(rows, "hard"), episodes: episodes(rows).slice(0, 15), motion: motionStats(rows), motionNoDance: motionStats(rows, dance), feet: footStats(rows, dance) };
}
if (MODE === "replay" || MODE === "both" || MODE === "all") {
  const { page, info } = await openPage(browser, REPLAY);
  const dur = await page.evaluate(() => window.capture.duration);
  const from = Number(opt("--from", 0)), to = Math.min(dur, Number(opt("--to", dur)));
  const rows = [];
  const t0 = Date.now();
  const shots = new Set(SHOTS.map((s) => Math.round(s * FPS)));
  for (let i = 0; i < Math.ceil(to * FPS); i++) {
    const t = i / FPS;
    const r = await page.evaluate(async (t) => {
      await window.capture.frame(t);
      return window.__probe.measure();
    }, t);
    if (t >= from) rows.push({ t: +t.toFixed(4), ...r });
    if (shots.has(i)) await page.screenshot({ path: `${OUT}/${LABEL}_t${(i / FPS).toFixed(2)}.jpg`, type: "jpeg", quality: 90, clip: { x: 300, y: 60, width: 520, height: 660 } });
    if (i % 300 === 0) console.log(`replay ${t.toFixed(1)}/${to.toFixed(1)} s  ${((Date.now() - t0) / 1000).toFixed(0)} s`);
  }
  const strokes = await page.evaluate(() => window.capture.character.talk?.strokes ?? []);
  const onsets = await page.evaluate(audioOnsets);
  await page.close();
  const dance = (r) => r.clip === "dance";
  result.replay = {
    info,
    pen: penStats(rows),
    hard: penStats(rows, "hard"),
    episodes: episodes(rows).slice(0, 15),
    motion: motionStats(rows),
    motionNoDance: motionStats(rows, dance),
    feet: footStats(rows, dance),
    sync: syncStats(strokes, onsets),
    strokes,
    strokesPerMin: { speech: +(strokes.length / (rows.filter((r) => r.spk).length / FPS / 60)).toFixed(1), session: +(strokes.length / (rows.length / FPS / 60)).toFixed(1), speechSeconds: +(rows.filter((r) => r.spk).length / FPS).toFixed(1) },
  };
  writeFileSync(`${OUT}/probe-${LABEL}-replay-rows.json`, JSON.stringify(rows));
  writeFileSync(`${OUT}/probe-${LABEL}.json`, JSON.stringify(result, null, 1));
}

if (MODE === "phrases" || MODE === "all") {
  // Each gesture phrase in context: speaking on its talk-rest loop, fired by a synthetic
  // voice (syllables at 4 Hz, every other one stressed), inertialized in and out.
  result.phrases = {};
  const pk = JSON.parse(readFileSync(OLD_PACK ? `${HEAD_DIR}/assets/clips/annie-blender/pack.json` : new URL("../assets/clips/annie-blender/pack.json", import.meta.url)));
  const names = opt("--phrases", Object.entries(pk.clips).filter(([, c]) => c.layer === "gesture").map(([n]) => n).join(",")).split(",");
  for (const name of names) {
    const { page } = await openPage(browser, "still=1");
    const ok = await page.evaluate((name) => {
      const c = window.capture.character, sc = c.talk?.sched;
      const p = sc?.phrases.find((x) => x.name === name);
      if (!p) return false;
      sc.phrases = [p];
      c.energy = p.rest === "low" ? "gentle" : p.energy === "excited" ? "high" : "moderate";
      const hear = c.hear.bind(c);
      c.hear = (now, f, us, ul) => {
        if (now < 1) return hear(now, f, us, ul);
        const ph = (now * 4) % 1, stressed = Math.floor(now * 4) % 2 === 0;
        return hear(now, { amp: 0.6, db: ph < 0.6 ? (stressed ? -14 : -22) : -60 }, us, ul);
      };
      return true;
    }, name);
    if (!ok) {
      await page.close();
      continue;
    }
    const rows = [];
    for (let i = 0; i < Math.round(5 * FPS); i++) {
      const t = i / FPS;
      const r = await page.evaluate(async (t) => {
        if (Math.abs(t - 1) < 1e-6) window.capture.forceSpeaking = true;
        await window.capture.frame(t);
        return window.__probe.measure();
      }, t);
      if (t >= 1) rows.push({ t: +t.toFixed(4), ...r });
    }
    const played = await page.evaluate(() => window.capture.character.talk.sched.strokes.length);
    await page.close();
    result.phrases[name] = { played, pen: penStats(rows), hard: penStats(rows, "hard"), motion: motionStats(rows) };
    const x = result.phrases[name];
    console.log(`${name.padEnd(18)} strokes ${played}  max ${String(x.pen.maxMm).padStart(5)} mm  >5mm ${String(x.pen.over5mm).padStart(3)}/${x.pen.frames}  hard max ${String(x.hard.maxMm).padStart(5)} >5mm ${String(x.hard.over5mm).padStart(3)}  spikes ${x.motion.spikes}  accMax ${x.motion.accMax}`);
  }
}

if (MODE === "clips" || MODE === "both" || MODE === "all") {
  result.clips = {};
  const packs = JSON.parse(readFileSync(OLD_PACK ? `${HEAD_DIR}/assets/clips/annie-blender/pack.json` : new URL("../assets/clips/annie-blender/pack.json", import.meta.url)));
  for (const clip of CLIPS) {
    const { page } = await openPage(browser, "still=1");
    // Talk loops: the TalkLayer buckets them by energy in pack order (as motion/blender/preview.mjs does).
    const tc = Object.entries(packs.clips).filter(([, v]) => v.layer === "talk");
    const me = tc.find(([n]) => n === clip)?.[1];
    const talk = me ? { energy: me.energy, idx: tc.filter(([, v]) => v.energy === me.energy).findIndex(([n]) => n === clip) } : null;
    const dur = await page.evaluate(({ clip, talk }) => {
      const c = window.capture.character;
      if (talk) {
        const s = c.talk.loops[talk.energy]?.[talk.idx];
        if (!s) return -1;
        c.talk.loops = { calm: [s], animated: [s], excited: [s] };
        c.talk.cur = null;
        return s.duration + 1;
      }
      if (c.meta[clip]?.loop) return c.meta[clip].duration + 0.5;
      return (c.meta[clip]?.duration ?? -1) + 0.8;
    }, { clip, talk });
    if (dur < 0) {
      await page.close();
      continue;
    }
    const rows = [];
    for (let i = 0; i < Math.round((1 + dur) * FPS); i++) {
      const t = i / FPS;
      const r = await page.evaluate(async ({ t, clip, talk }) => {
        const c = window.capture.character;
        if (Math.abs(t - 1) < 1e-6) {
          if (talk) window.capture.forceSpeaking = true;
          else if (clip.startsWith("listen")) c.listening = true;
          else if (c.meta[clip]?.loop && c.idleName !== clip) (c.idlePool = [clip]), (c.listenPool = []), (c.idleSince = -1e9);
          else c.playClip(clip, { energy: "moderate" });
        }
        await window.capture.frame(t);
        return window.__probe.measure();
      }, { t, clip, talk });
      if (t >= 1) rows.push({ t: +t.toFixed(4), ...r });
    }
    await page.close();
    result.clips[clip] = { pen: penStats(rows), hard: penStats(rows, "hard"), episodes: episodes(rows).slice(0, 3), motion: motionStats(rows), feet: footStats(rows) };
    const p = result.clips[clip].pen, hp = result.clips[clip].hard;
    console.log(`${clip.padEnd(18)} max ${String(p.maxMm).padStart(5)} mm  >5mm ${String(p.over5mm).padStart(3)}/${p.frames}  hard max ${String(hp.maxMm).padStart(5)} >5mm ${String(hp.over5mm).padStart(3)}  spikes ${result.clips[clip].motion.spikes}  acc>4 ${result.clips[clip].motion.pops}  accMax ${result.clips[clip].motion.accMax}`);
  }
}
await browser.close();

writeFileSync(`${OUT}/probe-${LABEL}.json`, JSON.stringify(result, null, 1));
if (result.replay) {
  const r = result.replay;
  console.log(`\nREPLAY (${r.pen.frames} frames): penetration max ${r.pen.maxMm} mm, p99 ${r.pen.p99Mm} mm, frames >5 mm ${r.pen.over5mm}, >10 mm ${r.pen.over10mm}, >20 mm ${r.pen.over20mm}; by part ${JSON.stringify(r.pen.byPartMaxMm)}`);
  if (r.hard) console.log(`  beyond loose-cloth give: max ${r.hard.maxMm} mm, p99 ${r.hard.p99Mm} mm, frames >5 mm ${r.hard.over5mm}, >10 mm ${r.hard.over10mm}; by part ${JSON.stringify(r.hard.byPartMaxMm)}`);
  console.log("worst episodes:");
  for (const e of r.episodes.slice(0, 10)) console.log(`  ${e.t0.toFixed(2)}-${e.t1.toFixed(2)} s  ${e.maxMm} mm @${e.at.toFixed(2)} ${e.what}  clip ${e.clip}${e.phrase ? " phrase " + e.phrase : ""}`);
  console.log(`pops: ${r.motion.spikes} isolated spikes (excl. dance ${r.motionNoDance.spikes}); acc > ${POP_T} deg/frame^2: ${r.motion.pops} in ${r.motion.popFrames} frames (excl. dance ${r.motionNoDance.pops}); acc p99 ${r.motion.accP99}, max ${r.motion.accMax}; worst ${JSON.stringify(r.motion.worstBones)}`);
  console.log(`hand acc p99 ${r.motion.handAccP99} mm/f^2, max ${r.motion.handAccMax}; feet (excl. dance) ${JSON.stringify(r.feet)}`);
  if (r.sync) {
    const f = (x) => `n ${x.n} |off| p50 ${(x.p50 * 1000).toFixed(0)} ms p90 ${(x.p90 * 1000).toFixed(0)} ms, median ${(x.medianSigned * 1000).toFixed(0)} ms, <=100 ms ${Math.round(x.within100ms * 100)}%`;
    console.log(`strokes: ${r.strokes.length} (${r.strokesPerMin.speech}/min of speech over ${r.strokesPerMin.speechSeconds} s; ${r.strokesPerMin.session}/min of session); multi-stroke beats ${r.strokes.filter((s) => s.k > 0).length}`);
    console.log(`sync (${r.sync.onsets} onsets, ${r.sync.strong} strong): strokes->onset ${f(r.sync.strokes)}\n  strokes->strong ${f(r.sync.strokesToStrong)}\n  random->onset ${f(r.sync.randomToAll)}\n  random->strong ${f(r.sync.randomToStrong)}\n  hist ${JSON.stringify(r.sync.strokes.hist)}`);
  }
}
console.log(`wrote ${OUT}/probe-${LABEL}.json`);
