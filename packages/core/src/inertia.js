import * as THREE from "three";

// Inertialization (Bollo, GDC 2016/2018). On a source switch the pose keeps what was on
// screen as an offset from the new source, and the offset (with its velocity) decays to zero.
// The new motion plays at full fidelity from its first frame, velocity stays continuous, and
// two poses are never averaged (averaging a hands-front pose with a hands-down one is what
// swept the arms through the body). decay() is the critically damped spring, for callers
// that relax a correction without a fixed end (the collision guard).

const MAX_V = 25; // rad/s (or m/s): a glitchy velocity estimate must not fling the pose

// Critically damped decay toward 0 over dt (exact), components i..i+n of x (value) and v (velocity).
export function decay(x, v, i, n, halflife, dt) {
  const y = (2 * Math.LN2) / halflife;
  const e = Math.exp(-y * dt);
  for (let k = i; k < i + n; k++) {
    const j = v[k] + x[k] * y;
    x[k] = e * (x[k] + j * dt);
    v[k] = e * (v[k] - j * y * dt);
  }
}

// Rotation vector (axis * angle, shortest arc) of a unit quaternion, into out[i..i+2].
export function qlog(q, out, i = 0) {
  let { x, y, z, w } = q;
  if (w < 0) (x = -x), (y = -y), (z = -z), (w = -w);
  const s = Math.hypot(x, y, z);
  const k = s < 1e-9 ? 2 : (2 * Math.atan2(s, w)) / s;
  out[i] = x * k, out[i + 1] = y * k, out[i + 2] = z * k;
  return out;
}

export function qexp(v, i, q) {
  const a = Math.hypot(v[i], v[i + 1], v[i + 2]);
  const s = a < 1e-9 ? 0.5 : Math.sin(a / 2) / a;
  return q.set(v[i] * s, v[i + 1] * s, v[i + 2] * s, Math.cos(a / 2));
}

// A pose over a fixed bone list: local quaternions plus the hips position.
export class Pose {
  constructor(n) {
    this.q = Array.from({ length: n }, () => new THREE.Quaternion());
    this.hips = new THREE.Vector3();
  }
  copy(p) {
    for (let i = 0; i < this.q.length; i++) this.q[i].copy(p.q[i]);
    this.hips.copy(p.hips);
    return this;
  }
}

// Per-bone offset state. On a switch the offset (and its velocity) is taken over by a quintic
// that reaches zero with zero velocity and acceleration after `blend` seconds (Bollo); it keeps
// the acceleration of a blend still running, so switches in quick succession stay smooth. (A
// critically damped spring fast enough to finish in ~0.35 s puts most of its acceleration
// into the first 30 fps frame, which reads as a small pop.) transition(a, b, dt): the sources
// just switched; a and b are the new sources' pose one step ago and now. apply(p, dt) writes
// offset * p into p.
export const BLEND = 0.36;

export class Inertializer {
  constructor(n, blend = BLEND) {
    this.n = n;
    this.blend = blend;
    const m = n * 3 + 3; // rotation vectors per bone, then the hips offset
    this.coef = new Float32Array(m * 6);
    this.tau = new Float32Array(n + 1).fill(Infinity); // time into each bone's blend
    this.dur = new Float32Array(n + 1);
    this.out = new Pose(n); // last displayed pose and its velocity
    this.outV = new Float32Array(m);
    this.primed = false;
    this.off = new Float32Array(m);
    this._q = new THREE.Quaternion();
    this._r = new Float32Array(3);
    this._i = new THREE.Quaternion();
  }

  transition(a, b, dt) {
    if (!this.primed || !(dt > 0)) return;
    const q = this._q, r = this._r, H = this.n * 3;
    const x = new Float32Array(3), v = new Float32Array(3);
    for (let i = 0; i <= this.n; i++) {
      if (i < this.n) {
        const ai = this._i.copy(a.q[i]).invert();
        qlog(q.copy(this.out.q[i]).multiply(ai), x, 0);
        qlog(q.copy(b.q[i]).multiply(ai), r, 0);
        for (let k = 0; k < 3; k++) v[k] = clampV(this.outV[i * 3 + k] - r[k] / dt);
      } else {
        for (let k = 0; k < 3; k++) {
          const ax = a.hips.getComponent(k);
          x[k] = this.out.hips.getComponent(k) - ax;
          v[k] = clampV(this.outV[H + k] - (b.hips.getComponent(k) - ax) / dt);
        }
      }
      // Heading home fast would overshoot: shorten the blend (Bollo's rule).
      const X = Math.hypot(x[0], x[1], x[2]);
      const V = X > 1e-9 ? (v[0] * x[0] + v[1] * x[1] + v[2] * x[2]) / X : 0;
      const T = V < 0 ? Math.min(this.blend, Math.max(0.12, (-5 * X) / V)) : this.blend;
      for (let k = 0; k < 3; k++) {
        const j = i * 3 + k, c = this.coef, o = j * 6;
        const a0 = this.tau[i] < this.dur[i] ? acc(c, o, this.tau[i]) : 0;
        const x0 = x[k], v0 = v[k];
        c[o] = x0;
        c[o + 1] = v0;
        c[o + 2] = a0 / 2;
        c[o + 3] = -(20 * x0 + 12 * v0 * T + 3 * a0 * T * T) / (2 * T ** 3);
        c[o + 4] = (30 * x0 + 16 * v0 * T + 3 * a0 * T * T) / (2 * T ** 4);
        c[o + 5] = -(12 * x0 + 6 * v0 * T + a0 * T * T) / (2 * T ** 5);
      }
      this.tau[i] = 0;
      this.dur[i] = T;
    }
  }

  apply(p, dt) {
    const q = this._q, r = this._r, H = this.n * 3, off = this.off;
    for (let i = 0; i <= this.n; i++) {
      if (!(this.tau[i] < this.dur[i])) continue;
      this.tau[i] += dt;
      const t = Math.min(this.tau[i], this.dur[i]);
      for (let k = 0; k < 3; k++) off[i * 3 + k] = t >= this.dur[i] ? 0 : val(this.coef, (i * 3 + k) * 6, t);
      if (i < this.n) p.q[i].premultiply(qexp(off, i * 3, q));
      else p.hips.x += off[H], p.hips.y += off[H + 1], p.hips.z += off[H + 2];
    }
    if (this.primed && dt > 0) {
      for (let i = 0; i < this.n; i++) {
        qlog(q.copy(p.q[i]).multiply(this._i.copy(this.out.q[i]).invert()), r, 0);
        for (let k = 0; k < 3; k++) this.outV[i * 3 + k] = r[k] / dt;
      }
      for (let k = 0; k < 3; k++) this.outV[H + k] = (p.hips.getComponent(k) - this.out.hips.getComponent(k)) / dt;
    }
    this.out.copy(p);
    this.primed = true;
    return p;
  }
}

const val = (c, o, t) => c[o] + t * (c[o + 1] + t * (c[o + 2] + t * (c[o + 3] + t * (c[o + 4] + t * c[o + 5]))));
const acc = (c, o, t) => 2 * c[o + 2] + t * (6 * c[o + 3] + t * (12 * c[o + 4] + t * 20 * c[o + 5]));
const clampV = (v) => Math.max(-MAX_V, Math.min(MAX_V, v));
