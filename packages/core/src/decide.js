import { floorDecide } from "./floor.js";
import { listenState, replyState } from "./instinct.js";

// Decision providers. Every provider resolves (never throws) to
// {answers, latency_ms, source}; a miss falls through to the floor so the
// character never waits.

export class FloorDecider {
  name = "floor";
  async decide(kind, ctx, questions) {
    const t0 = performance.now();
    const text = kind === "listen" ? ctx.userText : ctx.assistantText;
    const r = floorDecide(text, questions);
    return { ...r, latency_ms: performance.now() - t0, source: "floor" };
  }
}

// Jev through the broker (TypeSafe rejects browser origins, so a hop is mandatory).
export class BrokerDecider {
  name = "jev";
  constructor({ url, getToken, floor = new FloorDecider() }) {
    this.url = url.replace(/\/$/, "");
    this.getToken = getToken;
    this.floor = floor;
    this.failures = 0;
    this.pausedUntil = 0;
    this.disabled = false;
  }
  async decide(kind, ctx, questions) {
    const now = performance.now();
    if (this.disabled || now < this.pausedUntil) return this.floor.decide(kind, ctx, questions);
    const state = kind === "listen" ? listenState(ctx) : replyState(ctx);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), kind === "listen" ? 700 : 900);
    try {
      const res = await fetch(`${this.url}/decide`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: this.getToken?.(), state, questions }),
        signal: ctrl.signal,
      });
      if (res.status === 401 || res.status === 403) this.disabled = true;
      if (!res.ok) throw new Error(`decide ${res.status}`);
      const body = await res.json();
      this.failures = 0;
      return { answers: body.answers, usage: body.usage, latency_ms: performance.now() - now, source: "jev" };
    } catch {
      if (++this.failures >= 3) {
        this.pausedUntil = performance.now() + 30_000;
        this.failures = 0;
      }
      return this.floor.decide(kind, ctx, questions);
    } finally {
      clearTimeout(timer);
    }
  }
}
