import * as THREE from "three";

// A quiet, voice-app HUD: a transcript that streams as the models speak, Jev's picks
// as small chips on the message that caused them (and pinned beside her as they
// land), and a conversation bar with an orb and a live waveform on which each
// decision leaves a dot. Built from bus events, so live, scripted and replayed
// sessions look the same. All motion is driven by the stage clock (capture-exact).

const $ = (id) => document.getElementById(id);
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const ease = (t) => 1 - Math.pow(1 - clamp(t), 3);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const pretty = (s) => String(s).replace(/_/g, " ");

const MAX_MSGS = 7;
const TAG_LIFE = 2.4;
const BAR_STEP = 1 / 24; // one waveform bar per 1/24 s
const BARS = 72; // ~3 s of history

function topOf(a) {
  if (!a) return null;
  if (a.type === "noul") return { choice: "yes", p: a.noul ?? a.probabilities?.yes ?? 0 };
  return { choice: a.choice, p: a.confidence ?? a.probabilities?.[a.choice] ?? 0 };
}

export class Hud {
  constructor({ bus, character, camera }) {
    Object.assign(this, { bus, character, camera });
    this.now = 0;
    this.msgs = [];
    this.cur = { user: null, annie: null };
    this.tags = [];
    this.bars = []; // {t, who, level, mark?}
    this.nextBar = 0;
    this.orb = { level: 0, hue: 0, who: "idle" };
    this.v = new THREE.Vector3();
    this.wave = $("wave");
    this.wctx = this.wave.getContext("2d");
    this.octx = $("orb").getContext("2d");
    this.pendingMark = null;

    bus.on("user.partial", ({ text }) => this.#say("user", text, false));
    bus.on("user.final", ({ text }) => this.#say("user", text, true));
    bus.on("assistant.audio.start", () => this.#open("annie"));
    bus.on("assistant.partial", ({ text }) => this.#say("annie", text, false));
    bus.on("assistant.final", ({ text }) => this.#say("annie", text, true));
    bus.on("bargein", () => {
      const m = this.cur.annie ?? this.msgs.findLast((x) => x.who === "annie");
      if (m) m.cut = true;
    });
    bus.on("decision", (d) => this.#decision(d));
    bus.on("cue.face", (c) => this.#local(c));
  }

  setHonesty(text) {
    this.honesty = text; // kept for logs; not shown on screen
  }

  setMode(mode) {
    this.mode = mode;
  }

  // ---- transcript -----------------------------------------------------------
  #open(who) {
    if (this.cur[who]) return this.cur[who];
    const m = { who, text: "", done: false, born: this.now, chips: [], cut: false, el: null };
    this.msgs.push(m);
    this.cur[who] = m;
    while (this.msgs.length > MAX_MSGS) this.msgs.shift().el?.remove();
    return m;
  }
  #say(who, text, final) {
    const m = this.#open(who);
    if (text) m.text = text;
    if (final) {
      m.done = true;
      this.cur[who] = null;
    }
    m.dirty = true;
  }
  #chip(who, chip) {
    const m = this.cur[who] ?? this.msgs.findLast((x) => x.who === who);
    if (!m) return;
    const same = m.chips.find((c) => c.kind === chip.kind && c.label === chip.label);
    if (same) Object.assign(same, { meta: chip.meta });
    else m.chips.push({ ...chip, born: this.now });
    m.dirty = true;
  }

  // ---- decisions -------------------------------------------------------------
  #decision(d) {
    const a = d.answers ?? {};
    const acted = [];
    const face = topOf(a.face);
    if (face && d.acted.face) acted.push({ kind: "face", label: face.choice, p: face.p });
    if (d.kind === "reply") {
      const what = topOf(a.what);
      if (what && d.acted.clip) acted.push({ kind: "body", label: what.choice, p: what.p });
      if (d.acted.veto) acted.push({ kind: "body", label: "stays put", p: topOf(a.performs)?.p ?? 0 });
      const r = topOf(a.reaction);
      if (r && d.acted.reaction) acted.push({ kind: "react", label: r.choice, p: r.p });
    }
    if (!acted.length) return;
    const ms = `${Math.round(d.latency_ms)} ms`;
    for (const c of acted) this.#chip(d.kind === "listen" ? "user" : "annie", { kind: c.kind, label: pretty(c.label), meta: ms });
    const lead = acted[0];
    this.#tag(lead.kind, acted.map((c) => pretty(c.label)).join(" · "), `${lead.p.toFixed(2)} · ${ms}`);
    this.pendingMark = lead.kind;
  }

  #local(c) {
    this.#chip("annie", { kind: "local", label: c.name, meta: c.why });
    this.#tag("local", c.name, c.why);
    this.pendingMark = "local";
  }

  #tag(kind, text, meta) {
    const el = document.createElement("div");
    el.className = `tag ${kind}`;
    el.innerHTML = `<i></i><span>${esc(text)}</span><span class="meta">${esc(meta)}</span>`;
    $("tags").append(el);
    this.tags.unshift({ el, born: this.now });
    while (this.tags.length > 1) this.tags.pop().el.remove();
  }

  // ---- per frame -------------------------------------------------------------
  update(t, { speaking, userSpeaking, stats, userLevel = 0, annieLevel = 0 }) {
    this.now = t;
    // Your transcript lags your voice by ~1.4 s: open your bubble at voice onset so the
    // conversation stays in speaking order, and fill it in as the words arrive.
    if (userSpeaking && !this.wasUserSpeaking) this.#open("user").dirty = true;
    this.wasUserSpeaking = userSpeaking;
    this.#renderMsgs(t);
    this.#renderTags(t);

    const who = speaking ? "annie" : userSpeaking ? "user" : "idle";
    const level = who === "annie" ? annieLevel : who === "user" ? userLevel : 0;
    while (this.nextBar <= t) {
      this.bars.push({ t: this.nextBar, who, level, mark: this.pendingMark });
      this.pendingMark = null;
      this.nextBar += BAR_STEP;
    }
    while (this.bars.length > BARS) this.bars.shift();
    this.#renderWave(t);
    this.#renderOrb(t, who, level);
    if (stats) this.#stats(stats);
  }

  #renderMsgs(t) {
    const box = $("msgs");
    for (const m of this.msgs) {
      if (!m.el) {
        m.el = document.createElement("div");
        m.el.className = `msg ${m.who}`;
        box.append(m.el);
      }
      if (m.dirty) {
        m.dirty = false;
        const words = m.text.split(/\s+/).filter(Boolean);
        // The last few words of a message still streaming get a soft shimmer.
        const tail = m.done ? 0 : Math.min(3, words.length);
        const body = words.length
          ? esc(words.slice(0, words.length - tail).join(" ")) + (tail ? ` <span class="live">${esc(words.slice(-tail).join(" "))}</span>` : "")
          : `<span class="typing"><i></i><i></i><i></i></span>`;
        const chips = m.chips.map((c) => `<span class="chip ${c.kind}" data-born="${c.born}"><i></i>${esc(c.label)}<span class="meta">${esc(c.meta)}</span></span>`).join("");
        m.el.innerHTML =
          (m.who === "annie" ? `<div class="who"><i></i>Annie</div>` : "") +
          `<div class="text">${body || "&nbsp;"}${m.cut ? `<span class="cut">— interrupted</span>` : ""}</div>` +
          (chips ? `<div class="chips">${chips}</div>` : "");
      }
      const k = ease((t - m.born) / 0.4);
      m.el.style.opacity = String(k);
      m.el.style.transform = `translateY(${(1 - k) * 12}px)`;
      m.el.querySelectorAll(".typing i").forEach((d, i) => (d.style.opacity = String(0.25 + 0.75 * Math.max(0, Math.sin(t * 6 - i * 0.9)))));
      const live = m.el.querySelector(".live");
      if (live) live.style.backgroundPosition = `${100 - ((t * 60) % 100)}% 0`;
      for (const c of m.el.querySelectorAll(".chip")) c.style.opacity = String(ease((t - Number(c.dataset.born)) / 0.3));
    }
  }

  // Tags sit beside her head, projected from the head bone every frame.
  #renderTags(t) {
    const head = this.character.vrm.humanoid.getRawBoneNode("head");
    if (!head) return;
    head.getWorldPosition(this.v);
    this.v.y += 0.05;
    this.v.project(this.camera);
    const W = innerWidth, H = innerHeight;
    const x = ((this.v.x + 1) / 2) * W + W * 0.07;
    const y = ((1 - this.v.y) / 2) * H;
    this.tags = this.tags.filter((g) => (t - g.born > TAG_LIFE ? (g.el.remove(), false) : true));
    this.tags.forEach((g, i) => {
      const age = t - g.born;
      const a = ease(age / 0.25) * (1 - ease((age - TAG_LIFE + 0.45) / 0.45));
      g.el.style.left = `${x + (1 - ease(age / 0.3)) * 14}px`;
      g.el.style.top = `${y + i * H * 0.05}px`;
      g.el.style.opacity = String(a * (i ? 0.5 : 1));
    });
  }

  // ElevenLabs-style live waveform: rounded bars scroll left; Jev leaves a dot.
  #renderWave(t) {
    const c = this.wave, g = this.wctx;
    const dpr = devicePixelRatio;
    const W = c.clientWidth * dpr, H = c.clientHeight * dpr;
    if (c.width !== W || c.height !== H) (c.width = W), (c.height = H);
    g.clearRect(0, 0, W, H);
    const n = BARS, gap = W / n, bw = Math.max(2, gap * 0.55), mid = H * 0.58;
    const shift = ((t - (this.bars.at(-1)?.t ?? t)) / BAR_STEP) * gap;
    const grad = g.createLinearGradient(0, 0, W, 0);
    grad.addColorStop(0, "#f9a8d4");
    grad.addColorStop(0.5, "#a78bfa");
    grad.addColorStop(1, "#7dd3fc");
    this.bars.forEach((b, i) => {
      const x = W - (this.bars.length - i) * gap - shift + gap / 2;
      if (x < 0) return;
      const fade = clamp(x / (W * 0.25));
      const h = b.who === "idle" ? bw : Math.max(bw, (H * 0.8) * (0.15 + 0.85 * clamp(b.level)));
      g.globalAlpha = fade * (b.who === "idle" ? 0.35 : 1);
      g.fillStyle = b.who === "annie" ? grad : b.who === "user" ? "#1a1a19" : "#b9b8b3";
      g.beginPath();
      g.roundRect(x - bw / 2, mid - h / 2, bw, h, bw / 2);
      g.fill();
      if (b.mark) {
        g.globalAlpha = fade;
        g.fillStyle = { face: "#f472b6", body: "#8b5cf6", react: "#38bdf8", local: "#f59e0b" }[b.mark];
        g.beginPath();
        g.arc(x, H * 0.08 + 3, 3, 0, Math.PI * 2);
        g.fill();
      }
    });
    g.globalAlpha = 1;
  }

  // The orb: soft pastel blobs that swirl and swell with whoever is speaking.
  #renderOrb(t, who, level) {
    const g = this.octx, S = 112, R = S / 2;
    const o = this.orb;
    o.level += (level - o.level) * (level > o.level ? 0.35 : 0.12);
    g.clearRect(0, 0, S, S);
    g.save();
    g.beginPath();
    g.arc(R, R, R - 2, 0, Math.PI * 2);
    g.clip();
    const pal = who === "user" ? ["#3f3f3c", "#8a8984", "#d6d5d1"] : ["#f9a8d4", "#a78bfa", "#7dd3fc"];
    g.fillStyle = who === "user" ? "#e9e8e5" : "#f5f0ff";
    g.fillRect(0, 0, S, S);
    const sp = 0.6 + o.level * 2.2;
    for (let i = 0; i < 3; i++) {
      const a = t * sp * (0.7 + i * 0.25) + (i * Math.PI * 2) / 3;
      const r = R * (0.55 + 0.18 * Math.sin(t * 1.3 + i) + o.level * 0.35);
      const x = R + Math.cos(a) * R * 0.32, y = R + Math.sin(a) * R * 0.32;
      const rg = g.createRadialGradient(x, y, 0, x, y, r);
      rg.addColorStop(0, pal[i]);
      rg.addColorStop(1, "rgba(255,255,255,0)");
      g.globalAlpha = who === "idle" ? 0.55 : 0.9;
      g.fillStyle = rg;
      g.fillRect(0, 0, S, S);
    }
    g.restore();
    g.globalAlpha = 1;
    g.strokeStyle = "rgba(13,13,13,0.06)";
    g.lineWidth = 2;
    g.beginPath();
    g.arc(R, R, R - 2, 0, Math.PI * 2);
    g.stroke();
  }

  #stats(s) {
    const sorted = [...s.latencies].sort((a, b) => a - b);
    const p50 = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
    const cost = (s.tokens / 1e6) * 0.042;
    const html = `<span><b>${s.decisions}</b>decisions</span><span><b>${Math.round(p50)} ms</b>p50</span><span><b>$${cost.toFixed(4)}</b>Jev</span>`;
    if (html !== this.lastStats && $("stats")) $("stats").innerHTML = this.lastStats = html;
    this.summary = { decisions: s.decisions, p50, tokens: s.tokens, cost };
  }

  overlays(t, end) {
    const title = $("title-card"), outro = $("outro-card");
    title.style.opacity = String(t < 1.6 ? 1 : clamp(1 - (t - 1.6) / 0.7));
    const o = clamp((t - (end - 3.6)) / 0.8);
    outro.style.opacity = String(o);
    if (o > 0 && this.summary) {
      const s = this.summary;
      $("outro-stats").innerHTML = `<b>${s.decisions}</b> decisions · <b>${Math.round(s.p50)} ms</b> p50 · <b>$${s.cost.toFixed(4)}</b> of Jev`;
    }
  }
}
