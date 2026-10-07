// One event bus shared by every provider and layer. Payloads are plain objects.
export class Bus extends EventTarget {
  emit(type, detail = {}) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
  on(type, fn) {
    const h = (e) => fn(e.detail);
    this.addEventListener(type, h);
    return () => this.removeEventListener(type, h);
  }
}

// Clocks are injectable so the capture pipeline can drive time deterministically.
export const realClock = { now: () => performance.now() / 1000 };

export const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
export const lerp = (a, b, t) => a + (b - a) * t;
export const smoothstep = (t) => t * t * (3 - 2 * t);
export const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

// Deterministic PRNG so the idle pool, gains, and blinks replay identically in capture mode.
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// CJK characters carry roughly three Latin characters of meaning (review: pacing rules).
export function textWeight(s) {
  let w = 0;
  for (const ch of s) w += /[぀-ヿ㐀-鿿가-힯]/.test(ch) ? 3 : 1;
  return w;
}
