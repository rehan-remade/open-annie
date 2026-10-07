import { Pacer, listenQuestions, replyQuestions, gateListen, gateReply } from "./instinct.js";

// Reactions are body language layered on whatever clip is playing.
const REACTION_MOTION = {
  agree: { gesture: "nod" },
  disagree: { gesture: "shake" },
  empathize: { gesture: "tilt" },
  celebrate: { clip: "clap" },
  recoil: { clip: "surprised_recoil" },
  ponder: { gesture: "tilt" },
  bashful: { gesture: "duck" },
  scoff: { gesture: "shake" },
};

// Instinct wiring: transcripts in, gated cues out. Decisions are stamped with the
// turn epoch they were asked in and applied at ask time + measured latency, so a
// virtual clock (capture mode) and wall time behave identically.
export class Annie {
  constructor({ bus, character, decider, clock }) {
    Object.assign(this, { bus, character, decider, clock });
    this.pacer = new Pacer();
    this.epoch = 0;
    this.history = [];
    this.userText = "";
    this.assistantText = "";
    this.lastReactionAt = -Infinity;
    this.inflight = new Set();
    this.due = [];
    this.stats = { decisions: 0, jev: 0, floor: 0, latencies: [], tokens: 0 };

    // While the user talks, the body switches to its attentive listen loops.
    bus.on("user.partial", ({ text }) => ((character.listening = true), this.#onUser(text, false)));
    bus.on("user.final", ({ text }) => this.#onUser(text, true));
    bus.on("assistant.partial", ({ text }) => this.#onAssistant(text, false));
    bus.on("assistant.final", ({ text }) => this.#onAssistant(text, true));
    bus.on("assistant.audio.start", () => {
      character.listening = false;
      this.pacer.resetTurn();
      this.assistantText = "";
      this.epoch++;
    });
    bus.on("bargein", () => {
      this.epoch++;
      this.due = [];
      character.bargeIn();
      this.bus.emit("cue.face", { name: "surprised", source: "local", why: "barge-in" });
    });
  }

  #onUser(text, final) {
    this.userText = text;
    const now = this.clock.now();
    if (final) this.history.push(`User: ${text}`);
    if (final || this.pacer.shouldAskListen(text, now)) {
      this.pacer.lastListen = now;
      this.#ask("listen", { final }, listenQuestions());
    }
  }

  #onAssistant(text, final) {
    this.assistantText = text;
    const now = this.clock.now();
    this.character.transcript?.(text, now); // runs ahead of the audio: gesture timing
    if (final) {
      this.history.push(`Annie: ${text}`);
      this.#ask("reply", { final: true }, replyQuestions());
    } else if (this.pacer.shouldAskReply(text, now)) {
      this.pacer.markReply(text, now);
      this.#ask("reply", { final: false }, replyQuestions());
    }
  }

  #ask(kind, meta, questions) {
    const askedAt = this.clock.now();
    const epoch = this.epoch;
    const ctx = { history: this.history, userText: this.userText, assistantText: this.assistantText };
    const snapshot = kind === "listen" ? ctx.userText : ctx.assistantText;
    if (kind === "reply" && !meta.final) this.pacer.inFlight = true;
    const p = this.decider.decide(kind, ctx, questions).then((r) => {
      if (!r) {
        if (kind === "reply" && !meta.final) this.pacer.inFlight = false;
        return;
      }
      // In flight until ask time + latency on *our* clock, so a replayed log paces
      // exactly like the live run it was recorded from.
      if (kind === "reply" && !meta.final) (this.pacer.inFlight = false), (this.pacer.busyUntil = askedAt + r.latency_ms / 1000);
      this.due.push({ at: askedAt + r.latency_ms / 1000, kind, meta, epoch, r, text: snapshot });
    });
    this.inflight.add(p);
    p.finally(() => this.inflight.delete(p));
  }

  // Capture mode awaits outstanding decisions before stepping the virtual clock.
  async settle() {
    while (this.inflight.size) await Promise.all([...this.inflight]);
  }

  update(now) {
    const ready = this.due.filter((d) => d.at <= now);
    if (!ready.length) return;
    this.due = this.due.filter((d) => d.at > now);
    for (const d of ready) this.#apply(d, now);
  }

  #apply({ kind, meta, epoch, r, text }, now) {
    const s = this.stats;
    s.decisions++;
    s[r.source] = (s[r.source] ?? 0) + 1;
    s.latencies.push(r.latency_ms);
    s.tokens += r.usage?.input_tokens ?? 0;
    const stale = kind === "reply" && epoch !== this.epoch;
    const acted = {};
    if (!stale) {
      const ch = this.character;
      if (kind === "listen") {
        const g = gateListen(r.answers);
        if (g.face && ch.setFace(g.face.name, g.face.intensity)) acted.face = g.face;
      } else {
        const g = gateReply(r.answers, {
          final: meta.final,
          partialActed: this.pacer.partialActed,
          reactionCooldownOk: now - this.lastReactionAt > 20,
        });
        if (g.energy) ch.energy = g.energy;
        if (g.clip && !g.veto && !this.pacer.clipsThisTurn.has(g.clip.name) && ch.playClip(g.clip.name)) {
          this.pacer.clipsThisTurn.add(g.clip.name);
          acted.clip = g.clip;
          if (!meta.final) this.pacer.partialActed = true;
        }
        if (g.face && ch.setFace(g.face.name, g.face.intensity)) acted.face = g.face;
        if (g.reaction) {
          const m = REACTION_MOTION[g.reaction.name];
          const done = m?.gesture ? ch.gesture(m.gesture) : m?.clip && !acted.clip && !this.pacer.clipsThisTurn.has(m.clip) ? ch.playClip(m.clip) : false;
          if (done) {
            acted.reaction = g.reaction;
            this.lastReactionAt = now;
          }
        }
        if (g.veto) acted.veto = true;
        acted.energy = g.energy;
      }
    }
    this.bus.emit("decision", { kind, final: !!meta.final, text, answers: r.answers, usage: r.usage, latency_ms: r.latency_ms, source: r.source, acted, stale });
  }
}
