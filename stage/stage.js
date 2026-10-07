import * as THREE from "three";
import { Bus } from "../packages/core/src/bus.js";
import { Character } from "../packages/core/src/character.js";
import { Annie } from "../packages/core/src/annie.js";
import { FloorDecider, BrokerDecider } from "../packages/core/src/decide.js";
import { MOUTH_LEAD } from "../packages/core/src/lipsync.js";
import { ScriptedVoice, buildTimeline } from "../packages/core/src/voice-mock.js";
import { ReplayVoice } from "../packages/core/src/voice-replay.js";
import { Hud } from "./hud.js";
import { createStage } from "./scene.js";
import { tuneSprings } from "../packages/core/src/cloth.js";

const params = new URLSearchParams(location.search);
const CAPTURE = params.has("capture");
const ROOT = new URL("../", import.meta.url).href;
if (CAPTURE) document.body.classList.add("capture");

// Replays a decision log recorded against live Jev, keyed by ask order and text,
// so re-renders are deterministic and cost nothing. Unmatched asks use the floor.
class RecordedDecider {
  name = "recorded";
  constructor(log) {
    this.log = log.map((e) => ({ ...e, used: false }));
    this.floor = new FloorDecider();
  }
  async decide(kind, ctx, questions) {
    const text = kind === "listen" ? ctx.userText : ctx.assistantText;
    const e = this.log.find((x) => !x.used && x.kind === kind && x.text === text);
    // No recorded twin: the live run never asked this, so neither do we (no invented cards).
    if (!e) return this.skipUnmatched ? null : this.floor.decide(kind, ctx, questions);
    e.used = true;
    return { answers: e.answers, usage: e.usage, latency_ms: e.latency_ms, source: e.source };
  }
}

async function pickDecider() {
  if (params.get("decisions")) {
    const log = await fetch(new URL(params.get("decisions"), ROOT)).then((r) => r.json());
    const d = new RecordedDecider(log);
    d.skipUnmatched = params.has("replay");
    return d;
  }
  if (params.get("broker")) {
    const url = params.get("broker");
    const token = params.get("token") ?? (await fetch(`${url}/dev-token`).then((r) => (r.ok ? r.json() : {})).catch(() => ({}))).token;
    return new BrokerDecider({ url, getToken: () => token });
  }
  return new FloorDecider();
}

// ---- scene ------------------------------------------------------------------
const canvas = document.getElementById("scene");
const { renderer, scene, camera, adopt } = createStage(canvas, { capture: CAPTURE });

// Camera moves with the story: push in for feelings, pull out for the dance.
const SHOTS = { wide: { d: 3.9, y: 1.02 }, mid: { d: 3.2, y: 1.12 }, close: { d: 2.35, y: 1.3 } };
// Keyed by the user line that set up the moment, so scripted and recorded-live runs share it.
const SHOT_AFTER = { "05_user_feelings": "close", "08_annie_dance": "wide", "09_user_backflip": "mid", "11_user_plug": "close" };
function shotFor(t, timeline) {
  const b = timeline.beats.findLast((x) => x.start - 0.4 <= t && (x.who === "user" || SHOT_AFTER[x.id]));
  if (!b) return SHOTS.mid;
  // In a live replay the dance happens in Annie's reply to 07, so pull out once she answers it.
  if (b.id === "07_user_dance") return t > b.end + 0.5 ? SHOTS.wide : SHOTS.mid;
  return SHOTS[SHOT_AFTER[b.id] ?? "mid"];
}
const cam = { d: 3.2, y: 1.12 };
function placeCamera(t, dt, timeline) {
  const s = shotFor(t, timeline);
  const k = 1 - Math.exp(-dt / 0.9);
  cam.d += (s.d - cam.d) * k;
  cam.y += (s.y - cam.y) * k;
  const aspect = camera.aspect;
  // Keep her left of centre on wide screens so the Instinct panel has room.
  const shift = aspect > 1.2 ? 0.34 * (cam.d / 3.2) : 0;
  const drift = 0.012;
  camera.position.set(shift + drift * Math.sin(t * 0.37), cam.y + 0.05 + drift * Math.sin(t * 0.53), cam.d);
  camera.lookAt(shift + drift * 0.5 * Math.sin(t * 0.29), cam.y - 0.02, 0);
}

// ---- boot -------------------------------------------------------------------
const bus = new Bus();
const clock = { t: 0, now: () => clock.t };
const [lines, plan, avatars, decider] = await Promise.all([
  fetch(new URL("demo/audio/lines.json", ROOT)).then((r) => r.json()),
  fetch(new URL("demo/plan.json", ROOT)).then((r) => r.json()),
  fetch(new URL("assets/avatars/index.json", ROOT)).then((r) => r.json()),
  pickDecider(),
]);
const REPLAY = params.get("replay");
const session = REPLAY ? await fetch(new URL(REPLAY, ROOT)).then((r) => r.json()) : null;
const voice = session
  ? new ReplayVoice({ bus, session, baseUrl: ROOT, setTime: (t) => (clock.t = t) })
  : new ScriptedVoice({ bus, timeline: buildTimeline(lines, plan), baseUrl: new URL("demo/audio", ROOT).href });
const timeline = voice.timeline;
const avatar = avatars.find((a) => a.id === params.get("avatar")) ?? avatars.find((a) => a.default);
// ?body=packA,packB overrides the installed body packs (A/B comparisons; "none" = procedural only).
const bodyParam = params.get("body");
const motion = bodyParam ? { packs: bodyParam === "none" ? [] : bodyParam.split(",").map((id) => new URL(`assets/clips/${id}/`, ROOT).href) } : {};
if (params.get("talk")) motion.talk = new URL(`assets/clips/${params.get("talk")}/`, ROOT).href;
// ?avatarUrl= loads any VRM (avatar comparisons); packs still come from assets/clips.
const avatarUrl = new URL(params.get("avatarUrl") ?? avatar.path, ROOT).href;
if (params.get("avatarUrl") && !motion.packs) {
  // Packs are normally found next to the avatar (assets/clips); point at them explicitly.
  const idx = await fetch(new URL("assets/clips/index.json", ROOT)).then((r) => r.json());
  motion.packs = (idx.body ?? []).map((id) => new URL(`assets/clips/${id}/`, ROOT).href);
  motion.talk ??= idx.talk ? new URL(`assets/clips/${idx.talk}/`, ROOT).href : undefined;
}
const [character] = await Promise.all([Character.load({ url: avatarUrl, scene, camera, seed: 11, motion, hide: params.get("outfit") === "full" || params.get("avatarUrl") ? null : avatar.hide }), voice.load()]);
// Loudness envelopes of the scripted user lines, so the waveform draws your real audio too.
const userEnv = {};
{
  const ctx = new OfflineAudioContext(1, 24000, 24000);
  await Promise.all(
    timeline.beats.filter((b) => b.who === "user").map(async (b) => {
      const buf = await fetch(new URL(`demo/audio/${b.file}`, ROOT)).then((r) => r.arrayBuffer()).catch(() => null);
      if (!buf) return;
      const x = (await ctx.decodeAudioData(buf)).getChannelData(0);
      const hop = Math.round(24000 / 60), env = [];
      for (let i = 0; i < x.length; i += hop) {
        let q = 0;
        for (let j = i; j < Math.min(x.length, i + hop); j++) q += x[j] * x[j];
        env.push(Math.min(1, Math.max(0, (10 * Math.log10(q / hop + 1e-12) + 45) / 30)));
      }
      userEnv[b.id] = env;
    }),
  );
}
const userLevelAt = (t) => {
  const b = timeline.beats.find((x) => x.who === "user" && t >= x.start && t < x.end);
  return b && userEnv[b.id] ? userEnv[b.id][Math.floor((t - b.start) * 60)] ?? 0 : 0;
};
character.vrm.scene.position.x = 0;
adopt(character.vrm.scene);
tuneSprings(character.vrm, { enabled: params.get("cloth") !== "default" }); // ?cloth=default: as the rig ships
const annie = new Annie({ bus, character, decider, clock });
const shaper = character.lips; // mouth.js rig (HeadAudio visemes, energy fallback)
const hud = new Hud({ bus, voice, timeline, character, camera });
const decisionLog = [];
const events = [];
{
  const play = character.playClip.bind(character);
  character.playClip = (name, o) => {
    const ok = play(name, o);
    if (ok) events.push({ type: "clip", name, t: clock.t, rate: character.current.rate });
    return ok;
  };
}
bus.on("decision", (d) => decisionLog.push({ kind: d.kind, final: d.final, text: d.text, answers: d.answers, usage: d.usage, latency_ms: d.latency_ms, source: d.source }));
const sourceLabel = { jev: "live Jev decisions", recorded: "Jev decisions recorded live, replayed", floor: "local rule floor (no Jev key)" }[decider.name];
hud.setHonesty(
  session
    ? `recorded live session · Annie: GPT-Live-1 (voice ${session.voice}) · you: scripted lines performed by gpt-audio-1.5 (cedar), fed as the mic · ${sourceLabel} · lipsync, face and body computed in the browser`
    : `preview · Annie: scripted Kokoro-82M lines · you: gpt-audio-1.5 (cedar) lines · ${sourceLabel} · lipsync, face and body computed in the browser`,
);
hud.setMode(session ? "recorded" : "preview");

let last = 0;
// mode: "script" drives the scripted timeline; "idle" and "live" run on their own clocks.
function step(t, mode = "script") {
  const dt = Math.min(0.1, Math.max(0, t - last));
  last = t;
  clock.t = t;
  let f = null;
  let userSpeaking = false;
  let userLevel = 0;
  if (mode === "script") {
    voice.tick(t);
    f = voice.mouthAt(t + MOUTH_LEAD); // visual leads audio slightly (measured, lipsync-eval/)
    userSpeaking = timeline.beats.some((b) => b.who === "user" && t >= b.start && t < b.end);
    userLevel = userLevelAt(t);
  } else if (mode === "live") {
    f = live.tick(t, shaper.speaking);
    userSpeaking = live.userVoiced;
    userLevel = live.micLevel ?? (userSpeaking ? 0.5 : 0);
  }
  const mouth = shaper.update(f, t, dt || 1 / 60);
  character.setMouth(mouth, shaper.speaking || !!window.capture?.forceSpeaking);
  character.hear?.(t, f, userSpeaking, userLevel); // gesture timing + listening backchannels
  annie.update(t);
  character.update(t, dt);
  placeCamera(mode === "script" ? t : 0, dt, mode === "script" ? timeline : { beats: [] });
  if (!DIRECTED) renderer.render(scene, camera);
  hud.update(t, { speaking: shaper.speaking, userSpeaking, mouth, stats: annie.stats, userLevel, annieLevel: shaper.speaking ? f?.amp ?? 0 : 0 });
  if (mode === "script") hud.overlays(t, timeline.end);
}

// Live mode: real mic, GPT-Live via the broker, Jev with the session's token.
let live = null;
const LIVE = params.has("live") && params.get("broker");
// Directed recordings are replayed and rendered later; skip drawing so audio analysis runs at full rate.
const DIRECTED = !!(LIVE && params.get("direct"));
function liveCaptions() {
  // Present live transcripts through the same caption interface as the script.
  const beat = (who, s) => ({ who, start: -1, end: Infinity, words: s.text.trim().split(/\s+/).filter(Boolean).map((w) => ({ word: w, start_s: 0 })) });
  hud.voice = { activeBeats: () => [live.user.text && beat("user", live.user), live.asst.text && beat("annie", live.asst)].filter(Boolean) };
}

if (CAPTURE) {
  // Deterministic stepping: the capture script calls frame(t) for every output frame.
  window.capture = {
    duration: timeline.end,
    beats: timeline.beats.map(({ id, who, file, start, end }) => ({ id, who, file, start, end })),
    // What the mux lays down: every user line where it played, plus Annie's recorded track.
    audio: session
      ? [...session.userPlays.map((u) => ({ file: `demo/audio/${u.file}`, start: u.t })), { file: session.audio.file, start: session.audio.start }]
      : timeline.beats.map((b) => ({ file: `demo/audio/${b.file}`, start: b.start })),
    // ?still=1: no scripted timeline, just the character (clip comparisons).
    // capture.forceSpeaking drives the talk layer without audio.
    forceSpeaking: false,
    async frame(t) {
      step(t, params.has("still") ? "idle" : "script");
      await annie.settle();
      annie.update(t);
    },
    log: () => decisionLog,
    events: () => events,
    character,
    summary: () => ({ ...hud.summary, stats: annie.stats }),
  };
  step(0);
  document.body.dataset.ready = "1";
} else {
  const btn = document.getElementById("play");
  document.getElementById("title-card").style.opacity = "0";
  let mode = "idle";
  const t0 = performance.now() / 1000;
  const wall = () => performance.now() / 1000 - t0;
  const loop = () => {
    if (mode === "idle" || mode === "live") step(wall(), mode);
    if (!DIRECTED) requestAnimationFrame(loop);
  };
  if (DIRECTED) setInterval(loop, 16);
  else requestAnimationFrame(loop);
  if (LIVE) {
    btn.textContent = "🎙 Talk to Annie";
    hud.setHonesty("live · voice: GPT-Live-1 over WebRTC · decisions: Jev via broker · lipsync, face and body computed in the browser");
    hud.setMode("live");
  }
  if (LIVE && params.get("direct")) queueMicrotask(() => btn.onclick());
  btn.onclick = async () => {
    btn.hidden = true;
    if (LIVE) {
      const { GptLiveVoice } = await import("../packages/core/src/voice-gptlive.js");
      const liveClock = { now: wall };
      // ?direct=<plan>: scripted user lines go in through a synthetic mic and the session is recorded.
      const planUrl = params.get("direct");
      const plan = planUrl ? await fetch(new URL(planUrl, ROOT)).then((r) => r.json()) : null;
      const micCtx = plan ? new AudioContext() : null;
      const micDest = micCtx?.createMediaStreamDestination();
      if (micCtx) {
        // A faint room-noise floor (~ -60 dBFS). Digital silence lets Opus DTX stop
        // sending packets, and GPT-Live, clocked by input audio, stalls mid-sentence.
        const noise = micCtx.createBuffer(1, micCtx.sampleRate * 2, micCtx.sampleRate);
        const ch = noise.getChannelData(0);
        for (let i = 0; i < ch.length; i++) ch[i] = (Math.random() * 2 - 1) * 0.0017;
        const src = micCtx.createBufferSource();
        Object.assign(src, { buffer: noise, loop: true });
        src.connect(micDest);
        src.start();
      }
      live = new GptLiveVoice({
        bus, brokerUrl: params.get("broker"), clock: liveClock, voice: plan?.voice ?? "willow", mic: micDest?.stream,
        instructions: await fetch(new URL("persona/character.md", ROOT)).then((r) => r.text()),
      });
      try {
        await live.connect();
      } catch (e) {
        btn.hidden = false;
        btn.textContent = `Voice unavailable (${e.message}) · retry`;
        if (plan) window.liveRecord = { done: true, export: () => ({ error: e.message }) };
        return;
      }
      annie.clock = liveClock;
      annie.decider = new BrokerDecider({ url: params.get("broker"), getToken: () => live.token });
      liveCaptions();
      mode = "live";
      if (plan) {
        const { direct } = await import("./director.js");
        const out = await direct({ bus, live, plan, lines, wall, ctx: micCtx, dest: micDest, audioBase: new URL("demo/audio", ROOT).href });
        await annie.settle();
        window.liveRecord = { done: true, export: () => ({ ...out, decisions: decisionLog }) };
      }
      return;
    }
    mode = "script";
    const ctx = new AudioContext();
    const a0 = ctx.currentTime + 0.1;
    for (const b of timeline.beats) {
      const src = ctx.createBufferSource();
      src.buffer = await ctx.decodeAudioData(voice.audio[b.id].slice(0));
      src.connect(ctx.destination);
      src.start(a0 + b.start);
    }
    last = 0;
    const run = () => {
      const t = ctx.currentTime - a0;
      if (t >= 0) step(Math.min(t, timeline.end));
      if (t < timeline.end) requestAnimationFrame(run);
    };
    requestAnimationFrame(run);
  };
}
