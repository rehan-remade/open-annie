import { FACES } from "./faces.js";
import { CLIPS, ACTION_CLIPS } from "./clips.js";
import { textWeight } from "./bus.js";

// Instinct: the Jev question set, the pacer that decides when to ask, and the gates
// that decide whether an answer may act. Code keeps control; Jev only fills
// parameters code has already validated against the installed clip list.

export const REACTIONS = {
  agree: "nodding along, she agrees with or accepts what the other person said",
  disagree: "shaking her head, she declines, refuses, or says no to a request",
  empathize: "softening toward the other person because THEY are hurting",
  celebrate: "sharing the other person's good news or joy",
  recoil: "taken aback, startled, or cut off mid-sentence",
  ponder: "considering a question before answering",
  bashful: "flattered or embarrassed about herself",
  scoff: "dismissing something as silly or unlikely",
  none: "no distinct body-language reaction",
};

const faceCriteria = () => Object.fromEntries(Object.entries(FACES).map(([k, v]) => [k, v.when]));

export function listenQuestions() {
  return {
    face: {
      type: "choice",
      instructions: "Which facial expression would the listening friend naturally show at this moment, reacting to what the user is saying right now?",
      criteria: faceCriteria(),
    },
  };
}

export function replyQuestions(clips = ACTION_CLIPS) {
  return {
    performs: {
      type: "noul",
      instructions:
        "Is Annie committing to a physical action or gesture RIGHT NOW in her own current words? " +
        "No if the action is hypothetical, quoted, in the past, only offered, or declined. " +
        "Staying put, refusing, standing still, or saying she won't do something is NOT performing.",
    },
    what: {
      type: "choice",
      instructions: "Which single body clip best fits what Annie is doing as she says this?",
      criteria: { ...Object.fromEntries(clips.map((c) => [c, CLIPS[c].when])), none: "no clip fits, or she isn't acting" },
    },
    reaction: {
      type: "choice",
      instructions: "What body-language reaction is Annie showing, and toward whom? Be literal about who the feeling is directed at.",
      criteria: REACTIONS,
    },
    face: { type: "choice", instructions: "Which facial expression fits Annie as she says this?", criteria: faceCriteria() },
    energy: {
      type: "choice",
      instructions: "How energetic should her performance be?",
      criteria: { gentle: "soft, subdued, sad or tender", moderate: "normal conversation", high: "big, physical, excited" },
    },
  };
}

export function listenState(ctx) {
  return `Annie is a friendly 3D character in a live voice chat.\nRecent turns:\n${ctx.history.slice(-4).join("\n")}\nThe user is saying right now: "${ctx.userText}"`;
}

export function replyState(ctx) {
  return `Annie is a friendly 3D character in a live voice chat.\nThe user said: "${ctx.userText}"\nAnnie has said so far: "${ctx.assistantText}"`;
}

// ---- gates -------------------------------------------------------------------

const top = (a) => a && { choice: a.choice, p: a.confidence ?? a.probabilities?.[a.choice] ?? 0 };
// Jev noul answers are {type: "noul", noul: p} with no confidence field: gate on p.
const yes = (a) => (a?.type === "noul" ? a.noul ?? a.probabilities?.yes ?? null : null);

export function gateListen(answers) {
  const f = top(answers?.face);
  if (!f || f.p < 0.6) return {};
  return { face: { name: f.choice, intensity: 0.8, p: f.p } };
}

// A missing answer is never "no": it just can't act.
export function gateReply(answers, { final, partialActed, reactionCooldownOk }) {
  const out = {};
  const perf = yes(answers?.performs);
  const what = top(answers?.what);
  // Actions (dance) need a spoken commitment; expressive gestures (wave, clap) only a
  // confident pick. Measured: Jev scores "Oh, hi!" 0.08 on commitment but wave 0.95.
  const action = what && CLIPS[what.choice]?.kind === "action";
  const cMin = final ? 0.6 : action ? 0.85 : 0.8;
  const commitOk = !action || (perf != null && perf >= (final ? 0.5 : 0.6));
  if (what && CLIPS[what.choice] && what.p >= cMin && commitOk && (final || !partialActed)) {
    out.clip = { name: what.choice, p: what.p, performs: perf };
  }
  if (perf != null && perf <= 0.2 && (answers?.what?.probabilities?.none ?? 0) >= 0.7) out.veto = true;
  const r = top(answers?.reaction);
  if (final && r && r.choice !== "none" && r.p >= 0.75 && reactionCooldownOk) out.reaction = { name: r.choice, p: r.p };
  const f = top(answers?.face);
  if (f && f.p >= 0.6 && FACES[f.choice]) out.face = { name: f.choice, intensity: 1, p: f.p };
  const e = top(answers?.energy);
  if (e) out.energy = e.choice;
  return out;
}

// ---- pacer -------------------------------------------------------------------

export class Pacer {
  constructor() {
    this.resetTurn();
    this.lastListen = -Infinity;
  }
  resetTurn() {
    this.asks = 0;
    this.lastAskAt = -Infinity;
    this.lastAskLen = 0;
    this.inFlight = false;
    this.partialActed = false;
    this.clipsThisTurn = new Set();
  }
  // Assistant partials: one in flight, <= 4 per turn, >= 16 weight, >= 8 new, >= 450 ms apart.
  shouldAskReply(text, now) {
    const w = textWeight(text);
    return !this.inFlight && now >= (this.busyUntil ?? -Infinity) && this.asks < 4 && w >= 16 && w - this.lastAskLen >= 8 && now - this.lastAskAt >= 0.45;
  }
  markReply(text, now) {
    this.asks++;
    this.lastAskAt = now;
    this.lastAskLen = textWeight(text);
  }
  shouldAskListen(text, now) {
    return textWeight(text) >= 8 && now - this.lastListen >= 0.9;
  }
}
