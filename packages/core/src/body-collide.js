import * as THREE from "three";
import { decay, qexp, qlog } from "./inertia.js";

// ---- body surface -------------------------------------------------------------
// What the arms must stay out of: torso, hips, thighs and head. Each region is a polar
// height map around its humanoid bone, built from triangle samples of the skinned mesh
// (skin, top, cardigan, shorts, hair: whatever is outermost). A torso or thigh slice is
// star-shaped around the bone's long axis, so a cylinder map (outer radius per angle and
// height) describes it exactly; the head is a sphere map (radius per direction from its
// centre). The arms are left out of the regions. Queries give penetration depth (positive
// inside) and the outward normal. Frames are the normalized humanoid bones, so a map built
// at rest rides its bone rigidly (the runtime guard); the probe rebuilds them per frame
// from the skinned mesh.

export const REGIONS = ["hips", "spine", "chest", "upperChest", "neck", "head", "leftUpperLeg", "rightUpperLeg"];
const SHAPE = { head: "sph" };
const FOLD = { leftShoulder: "upperChest", rightShoulder: "upperChest" }; // collarbone skin belongs to the torso
const FINGER = /(Thumb|Index|Middle|Ring|Little)/;
const ELBOW_ZONE = 0.35; // upper-arm vertices this close to the elbow (fraction of the bone) count as elbow
const HEAD_REACH = 0.2; // m from the head joint: long hair (twin-tails) beyond this isn't head
const SKIN = /skin/i; // VRoid material names (Body_00_SKIN, Face_00_SKIN): the firm surface under clothes
const GIVE_REACH = 0.6; // forearm fraction from the elbow that may press into loose cloth

// Texture alpha lookup for cutout / blended materials, else null. VRoid clothes are often a
// longer base mesh cut to shape by the texture's alpha (a jacket that is a knee-length coat
// underneath), and the invisible part must not count as body.
const alphaCache = new WeakMap();
function alphaOf(material) {
  const map = material?.map ?? material?.uniforms?.map?.value;
  const cut = material?.alphaTest > 0 ? material.alphaTest : material?.transparent ? 0.5 : 0;
  const img = map?.image;
  if (!cut || !img?.width || typeof document === "undefined") return null;
  let a = alphaCache.get(img);
  if (!a) {
    const cv = document.createElement("canvas");
    cv.width = img.width, cv.height = img.height;
    const ctx = cv.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0);
    const px = ctx.getImageData(0, 0, img.width, img.height).data;
    a = new Uint8Array(img.width * img.height);
    for (let i = 0; i < a.length; i++) a[i] = px[i * 4 + 3];
    alphaCache.set(img, a);
  }
  const W = img.width, H = img.height, flip = map.flipY;
  return (u, v) => {
    u -= Math.floor(u), v -= Math.floor(v);
    const x = Math.min(W - 1, Math.floor(u * W)), y = Math.min(H - 1, Math.floor((flip ? 1 - v : v) * H));
    return a[y * W + x] >= cut * 255;
  };
}

// Arm part of a humanoid bone name, or null.
function partOf(b) {
  const m = /^(left|right)(.*)$/.exec(b ?? "");
  if (!m) return null;
  const side = m[1] === "left" ? 0 : 1;
  if (m[2] === "LowerArm") return { side, part: 0 };
  if (m[2] === "Hand") return { side, part: 1 };
  if (FINGER.test(m[2])) return { side, part: 2 };
  if (m[2] === "UpperArm") return { side, part: 3 };
  return null;
}

// Triangle samples of every skinned mesh, tagged with body regions or an arm part.
// skin() places them with the current bone matrices (call after matrixWorld updates).
export class BodySurface {
  constructor(vrm, { spacing = 0.012 } = {}) {
    const h = vrm.humanoid;
    const human = new Map();
    for (const [name, b] of Object.entries(h.rawHumanBones ?? h.humanBones)) if (b?.node) human.set(b.node, name);
    const humanOf = (bone) => {
      for (let n = bone; n; n = n.parent) if (human.has(n)) return human.get(n);
      return null;
    };
    this.meshes = [];
    this.boneNames = [];
    const boneId = new Map();
    const tri = [], bary = [], cls = [], mask = [], bone = [], firm = [], giveW = [];
    const v = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()];
    vrm.scene.traverse((o) => {
      if (!o.isSkinnedMesh || !o.geometry.index) return;
      const g = o.geometry, sk = o.skeleton;
      const si = g.attributes.skinIndex, sw = g.attributes.skinWeight, pos = g.attributes.position;
      const names = sk.bones.map(humanOf);
      const n = pos.count;
      // Bind space (the pose the mesh was skinned in) for vertices and joints alike.
      const joint = (name) => {
        const b = sk.bones.indexOf(h.getRawBoneNode(name));
        return b < 0 ? null : new THREE.Vector3().setFromMatrixPosition(new THREE.Matrix4().copy(sk.boneInverses[b]).invert());
      };
      // Per vertex: dominant humanoid bone (weights of non-humanoid bones such as hair or
      // bust springs go to their humanoid ancestor) -> region bit or arm part.
      const vcls = new Int16Array(n).fill(-1); // arm: side * 4 + part (0 forearm, 1 hand, 2 finger, 3 elbow)
      const vbone = new Int16Array(n).fill(-1); // arm: dominant humanoid bone
      const vgive = new Float32Array(n); // arm: how much this point may press into loose cloth (0..1)
      const vmask = new Uint16Array(n);
      const acc = new Map();
      const rest = new Float32Array(n * 3);
      const q = new THREE.Vector3();
      for (let i = 0; i < n; i++) q.fromBufferAttribute(pos, i).applyMatrix4(o.bindMatrix).toArray(rest, i * 3);
      for (let i = 0; i < n; i++) {
        acc.clear();
        for (let k = 0; k < 4; k++) {
          const w = sw.getComponent(i, k);
          if (w > 0) {
            const b = names[si.getComponent(i, k)];
            acc.set(b, (acc.get(b) ?? 0) + w);
          }
        }
        let best = null, bw = 0;
        for (const [b, w] of acc) if (w > bw) (best = b), (bw = w);
        const reg = REGIONS.indexOf(FOLD[best] ?? best);
        if (reg >= 0) {
          vmask[i] = 1 << reg;
          continue;
        }
        const p = partOf(best);
        if (!p) continue;
        const sd = p.side ? "right" : "left";
        const x = new THREE.Vector3().fromArray(rest, i * 3);
        if (p.part === 3) {
          const sh = joint(`${sd}UpperArm`), el = joint(`${sd}LowerArm`);
          if (!sh || !el || x.distanceTo(el) > ELBOW_ZONE * sh.distanceTo(el)) continue;
          vgive[i] = 1;
        } else if (p.part === 0) {
          // Along the forearm: the elbow end rests against the body, the wrist end must not sink.
          const el = joint(`${sd}LowerArm`), wr = joint(`${sd}Hand`);
          if (el && wr) {
            const ax = wr.clone().sub(el), s = x.clone().sub(el).dot(ax) / ax.lengthSq();
            vgive[i] = Math.min(1, Math.max(0, 1 - s / GIVE_REACH));
          }
        }
        vcls[i] = p.side * 4 + p.part;
        if (!boneId.has(best)) boneId.set(best, this.boneNames.push(best) - 1);
        vbone[i] = boneId.get(best);
      }
      const mi = this.meshes.length;
      const idx = g.index.array;
      const uv = g.attributes.uv;
      const mat = [].concat(o.material)[0];
      const opaque = uv && alphaOf(mat);
      const isFirm = SKIN.test(mat?.name ?? "") ? 1 : 0;
      const head = joint("head"), headBit = 1 << REGIONS.indexOf("head");
      const ps = new THREE.Vector3();
      for (let t = 0; t < g.index.count; t += 3) {
        const a = idx[t], b = idx[t + 1], c = idx[t + 2];
        const arm = vcls[a] >= 0 && vcls[a] >> 2 === vcls[b] >> 2 && vcls[b] >> 2 === vcls[c] >> 2 && vcls[b] >= 0 && vcls[c] >= 0;
        const body = vmask[a] && vmask[b] && vmask[c];
        if (!arm && !body) continue;
        v[0].fromArray(rest, a * 3), v[1].fromArray(rest, b * 3), v[2].fromArray(rest, c * 3);
        const L = Math.max(v[0].distanceTo(v[1]), v[1].distanceTo(v[2]), v[2].distanceTo(v[0]));
        const k = Math.max(1, Math.ceil(L / spacing));
        for (let i = 0; i <= k; i++) {
          for (let j = 0; i + j <= k; j++) {
            const b0 = i / k, b1 = j / k, b2 = 1 - b0 - b1;
            if (opaque && !opaque(b0 * uv.getX(a) + b1 * uv.getX(b) + b2 * uv.getX(c), b0 * uv.getY(a) + b1 * uv.getY(b) + b2 * uv.getY(c))) continue;
            let m = body ? vmask[a] | vmask[b] | vmask[c] : 0;
            if (m & headBit && head) {
              ps.set(0, 0, 0).addScaledVector(v[0], b0).addScaledVector(v[1], b1).addScaledVector(v[2], b2);
              if (ps.distanceTo(head) > HEAD_REACH) m &= ~headBit;
              if (body && !m) continue;
            }
            tri.push(mi, a, b, c);
            bary.push(b0, b1);
            firm.push(isFirm);
            if (arm) {
              const near = b0 >= b1 && b0 >= b2 ? a : b1 >= b2 ? b : c;
              cls.push(vcls[near]);
              bone.push(vbone[near]);
              giveW.push(isFirm ? 0 : b0 * vgive[a] + b1 * vgive[b] + b2 * vgive[c]); // a sleeve compresses cloth; bare skin in cloth shows
              mask.push(0);
            } else {
              cls.push(-1);
              bone.push(-1);
              giveW.push(0);
              mask.push(m);
            }
          }
        }
      }
      this.meshes.push({ mesh: o, si, sw, pos, n, world: new Float32Array(n * 3), mats: new Float32Array(sk.bones.length * 16) });
    });
    this.count = cls.length;
    this.tri = Uint32Array.from(tri);
    this.bary = Float32Array.from(bary);
    this.cls = Int16Array.from(cls); // arm samples: side * 4 + part; body samples: -1
    this.mask = Uint16Array.from(mask); // body samples: bit per region
    this.bone = Int16Array.from(bone); // arm samples: index into boneNames
    this.firm = Uint8Array.from(firm); // sample is skin (the body under any loose cloth)
    this.giveW = Float32Array.from(giveW); // arm samples: 0..1, may press into loose cloth (sleeves only)
    this.pos = new Float32Array(this.count * 3);
  }

  // World positions of all samples with the current bone matrices.
  skin(out = this.pos) {
    const M = new THREE.Matrix4();
    for (const m of this.meshes) {
      const sk = m.mesh.skeleton;
      for (let b = 0; b < sk.bones.length; b++) {
        M.multiplyMatrices(sk.bones[b].matrixWorld, sk.boneInverses[b]).multiply(m.mesh.bindMatrix);
        M.toArray(m.mats, b * 16);
      }
      const { si, sw, pos, world, mats: e } = m;
      for (let i = 0; i < m.n; i++) {
        const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
        let ox = 0, oy = 0, oz = 0;
        for (let k = 0; k < 4; k++) {
          const w = sw.getComponent(i, k);
          if (!w) continue;
          const o = si.getComponent(i, k) * 16;
          ox += w * (e[o] * x + e[o + 4] * y + e[o + 8] * z + e[o + 12]);
          oy += w * (e[o + 1] * x + e[o + 5] * y + e[o + 9] * z + e[o + 13]);
          oz += w * (e[o + 2] * x + e[o + 6] * y + e[o + 10] * z + e[o + 14]);
        }
        world[i * 3] = ox, world[i * 3 + 1] = oy, world[i * 3 + 2] = oz;
      }
    }
    const { tri, bary } = this;
    for (let s = 0; s < this.count; s++) {
      const w = this.meshes[tri[s * 4]].world;
      const a = tri[s * 4 + 1] * 3, b = tri[s * 4 + 2] * 3, c = tri[s * 4 + 3] * 3;
      const b0 = bary[s * 2], b1 = bary[s * 2 + 1], b2 = 1 - b0 - b1;
      out[s * 3] = b0 * w[a] + b1 * w[b] + b2 * w[c];
      out[s * 3 + 1] = b0 * w[a + 1] + b1 * w[b + 1] + b2 * w[c + 1];
      out[s * 3 + 2] = b0 * w[a + 2] + b1 * w[b + 2] + b2 * w[c + 2];
    }
    return out;
  }
}

// ---- polar height maps ----------------------------------------------------------
// Cylinder: rows of height `dy` along local y, `nT` angle bins around it; each bin keeps the
// outermost radius. Sphere: `nT` x `nP` direction bins around `center`. Empty bins are
// filled across small gaps only, so an open collar doesn't grow a phantom wall.
const NT = 64, DY = 0.01, NP = 32, GAP = 0.04, MAX_SLOPE = 2.5;

export class PolarMap {
  constructor(shape, lo, hi, center = null) {
    this.shape = shape;
    this.center = center ?? new THREE.Vector3();
    if (shape === "cyl") {
      this.y0 = lo - DY;
      this.rows = Math.max(1, Math.ceil((hi - lo) / DY) + 2);
    } else this.rows = NP;
    this.r = new Float32Array(this.rows * NT);
  }

  add(x, y, z) {
    let i, j, r;
    if (this.shape === "cyl") {
      j = Math.floor((y - this.y0) / DY);
      r = Math.hypot(x, z);
      i = Math.floor(((Math.atan2(x, z) + Math.PI) / (2 * Math.PI)) * NT);
    } else {
      x -= this.center.x, y -= this.center.y, z -= this.center.z;
      r = Math.hypot(x, y, z);
      if (r < 1e-6) return;
      j = Math.floor(((Math.asin(y / r) + Math.PI / 2) / Math.PI) * NP);
      i = Math.floor(((Math.atan2(x, z) + Math.PI) / (2 * Math.PI)) * NT);
    }
    if (j < 0 || j >= this.rows) return;
    i = Math.min(NT - 1, Math.max(0, i));
    j = Math.min(this.rows - 1, j);
    const k = j * NT + i;
    if (r > this.r[k]) this.r[k] = r;
  }

  // Fill empty bins between filled neighbours when the gap is short at that radius.
  finish() {
    const r = this.r;
    for (let j = 0; j < this.rows; j++) {
      const row = j * NT;
      const filled = [];
      for (let i = 0; i < NT; i++) if (r[row + i] > 0) filled.push(i);
      if (filled.length < 2) continue;
      const circ = this.shape === "cyl" ? 1 : Math.cos(((j + 0.5) / NP) * Math.PI - Math.PI / 2);
      for (let q = 0; q < filled.length; q++) {
        const a = filled[q], b = filled[(q + 1) % filled.length];
        const gap = (b - a + NT) % NT;
        if (gap <= 1) continue;
        const ra = r[row + a], rb = r[row + b];
        if ((gap * 2 * Math.PI * Math.max(ra, rb) * circ) / NT > GAP) continue;
        for (let s = 1; s < gap; s++) r[row + ((a + s) % NT)] = ra + ((rb - ra) * s) / gap;
      }
    }
    return this;
  }

  // Bilinear radius at fractional bin coords (u around, v along rows); 0 where empty.
  #at(u, v) {
    const fu = u - 0.5, fv = v - 0.5;
    const i0 = Math.floor(fu), j0 = Math.floor(fv);
    const a = fu - i0, b = fv - j0;
    const i0m = ((i0 % NT) + NT) % NT, i1m = (i0m + 1) % NT;
    const rows = this.rows, r = this.r;
    const row = (j) => (j < 0 || j >= rows ? -1 : j * NT);
    const r0 = row(j0), r1 = row(j0 + 1);
    const g = (rw, i) => (rw < 0 ? 0 : r[rw + i]);
    const v00 = g(r0, i0m), v10 = g(r0, i1m), v01 = g(r1, i0m), v11 = g(r1, i1m);
    // An empty corner would drag the surface inward: only interpolate over filled bins.
    let s = 0, w = 0;
    const acc = (val, wt) => val > 0 && ((s += val * wt), (w += wt));
    acc(v00, (1 - a) * (1 - b)), acc(v10, a * (1 - b)), acc(v01, (1 - a) * b), acc(v11, a * b);
    return w > 0.25 ? s / w : 0;
  }

  // Surface radius (bilinear) at a local point's bin; 0 where empty or out of range.
  radius(x, y, z) {
    if (this.shape === "cyl") {
      const v = (y - this.y0) / DY;
      if (v < 0 || v > this.rows) return 0;
      return this.#at(((Math.atan2(x, z) + Math.PI) / (2 * Math.PI)) * NT, v);
    }
    const dx = x - this.center.x, dy = y - this.center.y, dz = z - this.center.z;
    const rho = Math.hypot(dx, dy, dz);
    if (rho < 1e-6) return 0;
    return this.#at(((Math.atan2(dx, dz) + Math.PI) / (2 * Math.PI)) * NT, ((Math.asin(dy / rho) + Math.PI / 2) / Math.PI) * NP);
  }

  // Nearest surface point to a local point, searched over bins within `reach` metres:
  // the true penetration depth of a point well inside (the radial depth overstates it,
  // e.g. under a cardigan hem the way out is down or forward, not away from the spine).
  // Writes the surface point into s and returns the distance (Infinity if none).
  nearest(x, y, z, reach, s) {
    const dth = (2 * Math.PI) / NT;
    let best = Infinity, i0, j0, rows, kj, ki;
    if (this.shape === "cyl") {
      i0 = Math.floor(((Math.atan2(x, z) + Math.PI) / (2 * Math.PI)) * NT);
      j0 = Math.floor((y - this.y0) / DY);
      kj = Math.ceil(reach / DY);
      ki = Math.min(NT >> 1, Math.ceil(reach / (Math.max(Math.hypot(x, z), 0.03) * dth)) + 1);
    } else {
      const dx = x - this.center.x, dy = y - this.center.y, dz = z - this.center.z;
      const rho = Math.max(Math.hypot(dx, dy, dz), 0.03);
      i0 = Math.floor(((Math.atan2(dx, dz) + Math.PI) / (2 * Math.PI)) * NT);
      j0 = Math.floor(((Math.asin(dy / rho) + Math.PI / 2) / Math.PI) * NP);
      kj = Math.ceil(reach / (rho * (Math.PI / NP))) + 1;
      ki = Math.min(NT >> 1, Math.ceil(reach / (rho * dth)) + 1);
    }
    rows = this.rows;
    for (let j = Math.max(0, j0 - kj); j <= Math.min(rows - 1, j0 + kj); j++) {
      for (let di = -ki; di <= ki; di++) {
        const i = (((i0 + di) % NT) + NT) % NT;
        const R = this.r[j * NT + i];
        if (R <= 0) continue;
        const th = -Math.PI + (i + 0.5) * dth;
        let px, py, pz;
        if (this.shape === "cyl") (px = R * Math.sin(th)), (py = this.y0 + (j + 0.5) * DY), (pz = R * Math.cos(th));
        else {
          const ph = -Math.PI / 2 + ((j + 0.5) * Math.PI) / NP, cp = Math.cos(ph);
          px = this.center.x + R * cp * Math.sin(th), py = this.center.y + R * Math.sin(ph), pz = this.center.z + R * cp * Math.cos(th);
        }
        const d = Math.hypot(px - x, py - y, pz - z);
        if (d < best) (best = d), s.set(px, py, pz);
      }
    }
    return best;
  }

  // Slope of the map along u or v at (u, v) from filled bins only (one-sided at edges).
  #slope(u, v, du, dv, R) {
    const a = this.#at(u + du, v + dv), b = this.#at(u - du, v - dv);
    if (a > 0 && b > 0) return (a - b) / 2;
    if (a > 0) return a - R;
    if (b > 0) return R - b;
    return 0;
  }

  // Local point -> depth (positive = inside) and outward normal. Inside, the radial depth is
  // corrected by the surface slope (capped: a steep map edge isn't a wall); outside, the
  // radial clearance is returned as is, which is all the callers need there.
  query(x, y, z, n) {
    let R, depth, gv, gt, s, c, cp = 1, sp = 0;
    if (this.shape === "cyl") {
      const v = (y - this.y0) / DY;
      if (v < 0 || v > this.rows) return -Infinity;
      const th = Math.atan2(x, z);
      const u = ((th + Math.PI) / (2 * Math.PI)) * NT;
      R = this.#at(u, v);
      if (R <= 0) return -Infinity;
      depth = R - Math.hypot(x, z);
      s = Math.sin(th), c = Math.cos(th);
      gv = this.#slope(u, v, 0, 0.5, R) / (DY * 0.5); // dR/dy
      gt = this.#slope(u, v, 0.5, 0, R) / (Math.PI / NT) / Math.max(R, 1e-3); // (1/R) dR/dtheta
      // n ~ r_hat - dR/dy y_hat - (1/R) dR/dtheta theta_hat, theta_hat = (c, 0, -s)
      n.set(s - gt * c, -gv, c + gt * s);
    } else {
      const dx = x - this.center.x, dy = y - this.center.y, dz = z - this.center.z;
      const rho = Math.hypot(dx, dy, dz);
      if (rho < 1e-6) return Infinity;
      const ph = Math.asin(dy / rho), th = Math.atan2(dx, dz);
      const u = ((th + Math.PI) / (2 * Math.PI)) * NT, v = ((ph + Math.PI / 2) / Math.PI) * NP;
      R = this.#at(u, v);
      if (R <= 0) return -Infinity;
      depth = R - rho;
      cp = Math.cos(ph), sp = Math.sin(ph), s = Math.sin(th), c = Math.cos(th);
      gv = this.#slope(u, v, 0, 0.5, R) / (Math.PI / (2 * NP)) / R; // (1/R) dR/dphi
      gt = this.#slope(u, v, 0.5, 0, R) / (Math.PI / NT) / (R * Math.max(cp, 0.2)); // (1/(R cos phi)) dR/dtheta
      // rho_hat = (cp s, sp, cp c); phi_hat = (-sp s, cp, -sp c); theta_hat = (c, 0, -s)
      n.set(cp * s + gv * sp * s - gt * c, sp - gv * cp, cp * c + gv * sp * c + gt * s);
    }
    const len = Math.min(n.length(), MAX_SLOPE);
    n.normalize();
    return depth > 0 ? depth / len : depth;
  }
}

// Region maps from world sample positions, in the given region frames (world matrices of
// the normalized region bones): `map` is the outer surface, `firm` the skin under it (loose
// cloth stands off the skin; give() says how far an arm may press into it).
// Returns [{name, map, firm, inv, frame, sphere: {c, r}}].
export function buildRegionMaps(surface, positions, frames) {
  const out = [];
  const p = new THREE.Vector3();
  for (let r = 0; r < REGIONS.length; r++) {
    const frame = frames[r];
    if (!frame) continue;
    const inv = frame.clone().invert();
    const bit = 1 << r;
    let lo = Infinity, hi = -Infinity;
    const c = new THREE.Vector3();
    let n = 0;
    const local = [], firmIdx = [];
    for (let s = 0; s < surface.count; s++) {
      if (!(surface.mask[s] & bit)) continue;
      p.fromArray(positions, s * 3).applyMatrix4(inv);
      if (surface.firm[s]) firmIdx.push(local.length);
      local.push(p.x, p.y, p.z);
      lo = Math.min(lo, p.y), hi = Math.max(hi, p.y);
      c.add(p), n++;
    }
    if (!n) continue;
    c.multiplyScalar(1 / n);
    const shape = SHAPE[REGIONS[r]] ?? "cyl";
    const map = new PolarMap(shape, lo, hi, shape === "sph" ? c : null);
    const firm = new PolarMap(shape, lo, hi, shape === "sph" ? c : null);
    let rad = 0;
    for (let i = 0; i < local.length; i += 3) {
      map.add(local[i], local[i + 1], local[i + 2]);
      rad = Math.max(rad, Math.hypot(local[i] - c.x, local[i + 1] - c.y, local[i + 2] - c.z));
    }
    for (const i of firmIdx) firm.add(local[i], local[i + 1], local[i + 2]);
    out.push({ name: REGIONS[r], map: map.finish(), firm: firm.finish(), inv, frame: frame.clone(), sphere: { c: c.clone(), r: rad } });
  }
  return out;
}

// Clearance of a world point p to the body (negative = inside), the outward normal (into n),
// and how far loose cloth there lets an arm point press in: a share of how far the outer
// surface stands off the skin under it, the skin taken from whichever region covers the point
// (none where no skin is found, so tight clothes and bare skin stay hard). Regions need `inv`
// (world -> region frame) and `c`/`r2` (world bounding sphere) current.
export const GIVE = { share: 0.6, max: 0.035 };
const _l = new THREE.Vector3(), _n = new THREE.Vector3(), _m = new THREE.Vector3();
export function clearances(regions, p, n, out = {}) {
  let outer = Infinity, firm = Infinity, region = null;
  for (const r of regions) {
    if (p.distanceToSquared(r.c) > r.r2) continue;
    _l.copy(p).applyMatrix4(r.inv);
    const d = r.map.query(_l.x, _l.y, _l.z, _n);
    if (d !== -Infinity && -d < outer) {
      outer = -d;
      region = r;
      n.copy(_n).transformDirection(r.frame);
    }
  }
  // The skin only matters near the surface.
  if (outer < 0.03) {
    for (const r of regions) {
      if (p.distanceToSquared(r.c) > r.r2) continue;
      _l.copy(p).applyMatrix4(r.inv);
      const f = r.firm.query(_l.x, _l.y, _l.z, _m);
      if (f !== -Infinity) firm = Math.min(firm, -f);
    }
  }
  out.outer = outer;
  out.region = region;
  out.give = firm < Infinity ? Math.min(GIVE.max, GIVE.share * Math.max(0, firm - outer)) : 0;
  return out;
}

// ---- collision guard -----------------------------------------------------------------
// The last body step (docs/contracts.md, "Runtime body order"). Arm sample points (sleeve,
// hand, fingers, the elbow end of the upper arm), attached to their bones, are tested against
// the rest-pose region maps riding the torso, hips, thigh and head bones. Points inside are
// pushed out along the surface normal to a small margin by a damped Gauss-Newton IK on the
// shoulder (3 DOF, twist kept stiff) and the elbow hinge (limited, never past straight), with
// the hand's world orientation kept. Hands, fingers and the wrist end of the forearm are held
// to the outer surface; the elbow end of a sleeve may rest pressed into loose cloth (a puffy
// jacket side), or hanging arms would splay into an A-pose, while bare skin never sinks into
// anything. When only the elbow end is in, the wrist is kept where the animation had it, so
// the elbow swivels out instead of the arm swinging away.
// Targets come from the uncorrected pose through a soft hinge, so a hand approaching the body
// eases onto it instead of hitting a wall; every frame the correction relaxes toward zero on
// a critically damped spring and is re-projected, so it fades when not needed, never jitters,
// and a deliberate contact (hand on chest, finger on chin) rests on the surface.

const MARGIN = [0.005, 0.003, 0.002, 0.004]; // forearm, hand, finger, elbow: m of clearance (rest just outside)
const SOFT = 0.008; // m: the push eases in over about this much clearance
const RELAX = 0.12; // s: half-life of the correction's return to the animated pose
const ITERS = 5;
const TWIST_STIFF = 3; // shoulder twist costs this much more than swing
const ELBOW_STIFF = 2.5; // and the elbow: a forearm in the belly moves the whole arm forward
const DAMP = 0.02;
const REACH = 0.06; // m: points farther than this from the body can't be pushed into it by a correction
const KEEP = 0.6; // weight on keeping the wrist where the animation put it (the elbow swivels out instead)
const MAX_SWING = (40 * Math.PI) / 180;
const FLEX_MIN = (3 * Math.PI) / 180, FLEX_MAX = (150 * Math.PI) / 180;
const VOXEL = 0.02; // arm points are thinned to one per voxel (per part)

// Target clearance from the uncorrected clearance c0: untouched beyond m + SOFT, the margin
// m when deep, a C1 quadratic between (the push eases in instead of hitting a wall).
const softTarget = (c0, m) => (c0 >= m + SOFT ? c0 : c0 <= m - SOFT ? m : c0 + (m + SOFT - c0) ** 2 / (4 * SOFT));

export class CollisionGuard {
  // Build at rest (before any pose is applied): the maps and point offsets are rest-relative.
  constructor(vrm, { spacing = 0.012 } = {}) {
    this.vrm = vrm;
    const h = vrm.humanoid;
    vrm.scene.updateMatrixWorld(true);
    const surf = new BodySurface(vrm, { spacing });
    const rest = surf.skin(new Float32Array(surf.count * 3));
    const nodes = REGIONS.map((r) => h.getNormalizedBoneNode(r));
    this.regions = buildRegionMaps(surf, rest, nodes.map((n) => n?.matrixWorld)).map((m) => ({ ...m, node: h.getNormalizedBoneNode(m.name), frame: new THREE.Matrix4(), inv: new THREE.Matrix4(), c: new THREE.Vector3(), r2: (m.sphere.r + 0.05) ** 2 }));
    const hips = h.getNormalizedBoneNode("hips");
    const front = new THREE.Vector3(0, 0, vrm.meta?.metaVersion === "0" ? -1 : 1).applyQuaternion(hips.getWorldQuaternion(new THREE.Quaternion()));
    this.arms = [0, 1].map((side) => new ArmChain(vrm, surf, rest, side, front));
    this.stats = { before: 0, after: 0, active: false };
    this.enabled = true;
    this._q = {};
  }

  // Clearance of world point p (negative = inside by that much) and the outward normal;
  // this.give is how far loose cloth there lets a point press in (see clearances()).
  clearance(p, n) {
    const q = clearances(this.regions, p, n, this._q);
    this.give = q.give;
    return q.outer;
  }

  update(dt) {
    if (!this.enabled) return;
    const h = this.vrm.humanoid;
    h.normalizedHumanBonesRoot.updateMatrixWorld(true);
    for (const r of this.regions) r.frame.copy(r.node.matrixWorld), r.inv.copy(r.frame).invert(), r.c.copy(r.sphere.c).applyMatrix4(r.frame);
    let before = 0, after = 0, active = false;
    const corr = [];
    for (const arm of this.arms) {
      const s = arm.solve(this, dt);
      before = Math.max(before, s.before);
      after = Math.max(after, s.after);
      active ||= s.active;
      corr.push(+((Math.hypot(arm.x[0], arm.x[1], arm.x[2]) * 180) / Math.PI).toFixed(2), +((arm.x[3] * 180) / Math.PI).toFixed(2));
    }
    Object.assign(this.stats, { before: +(before * 1000).toFixed(1), after: +(after * 1000).toFixed(1), active, corr });
  }
}

class ArmChain {
  constructor(vrm, surf, rest, side, front) {
    const h = vrm.humanoid, s = side ? "right" : "left";
    this.side = side;
    this.ua = h.getNormalizedBoneNode(`${s}UpperArm`);
    this.parent = this.ua.parent;
    this.la = h.getNormalizedBoneNode(`${s}LowerArm`);
    this.hand = h.getNormalizedBoneNode(`${s}Hand`);
    // Points: thinned arm samples of this side, stored in their bone's rest frame.
    const seen = new Set();
    const pts = [];
    const inv = new Map();
    const p = new THREE.Vector3();
    for (let i = 0; i < surf.count; i++) {
      const c = surf.cls[i];
      if (c < 0 || c >> 2 !== side) continue;
      p.fromArray(rest, i * 3);
      const key = `${Math.floor(p.x / VOXEL)},${Math.floor(p.y / VOXEL)},${Math.floor(p.z / VOXEL)},${c & 3}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const node = h.getNormalizedBoneNode(surf.boneNames[surf.bone[i]]);
      if (!node) continue;
      if (!inv.has(node)) inv.set(node, node.matrixWorld.clone().invert());
      pts.push({ node, part: c & 3, gw: surf.giveW[i], local: p.clone().applyMatrix4(inv.get(node)) });
    }
    this.pts = pts;
    this.n = pts.length;
    this.w = new Float32Array(this.n * 3); // uncorrected world positions
    this.c = new Float32Array(this.n * 3); // corrected
    this.tau = new Float32Array(this.n); // target clearance (NaN: unconstrained)
    this.seg = Uint8Array.from(pts.map((q) => (q.part === 3 ? 0 : q.part === 0 ? 1 : 2))); // 0 upper arm, 1 forearm, 2 hand
    // Flexion sign: which way about the upper arm's y (the elbow hinge) bends the forearm forward.
    const yAxis = new THREE.Vector3(0, 1, 0).applyQuaternion(this.ua.getWorldQuaternion(new THREE.Quaternion()));
    const fore = new THREE.Vector3().setFromMatrixPosition(this.hand.matrixWorld).sub(new THREE.Vector3().setFromMatrixPosition(this.la.matrixWorld));
    this.flexSign = Math.sign(new THREE.Vector3().crossVectors(yAxis, fore).dot(front)) || 1;
    this.x = new Float32Array(4); // correction: shoulder rotation vector (parent frame), elbow hinge angle
    this.v = new Float32Array(4);
    this.tmp = { a: new THREE.Vector3(), n: new THREE.Vector3() };
  }

  solve(g, dt) {
    const { a, n } = this.tmp;
    const S = new THREE.Vector3().setFromMatrixPosition(this.ua.matrixWorld);
    const E = new THREE.Vector3().setFromMatrixPosition(this.la.matrixWorld);
    const W = new THREE.Vector3().setFromMatrixPosition(this.hand.matrixWorld);
    const Qp = this.parent.getWorldQuaternion(new THREE.Quaternion());
    const Qua = this.ua.getWorldQuaternion(new THREE.Quaternion());
    const Qla = this.la.getWorldQuaternion(new THREE.Quaternion());
    const Qh = this.hand.getWorldQuaternion(new THREE.Quaternion());
    const hinge0 = new THREE.Vector3(0, 1, 0).applyQuaternion(Qua);
    this.W0 = W;
    // Current flexion (signed angle about the hinge) for the limits.
    const flex0 = this.flexSign * signedAngle(new THREE.Vector3().subVectors(E, S), new THREE.Vector3().subVectors(W, E), hinge0);

    // Uncorrected clearances -> soft targets; points within reach of the body are candidates.
    let before = 0;
    const cand = (this.cand = []);
    for (let i = 0; i < this.n; i++) {
      const pt = this.pts[i];
      a.copy(pt.local).applyMatrix4(pt.node.matrixWorld).toArray(this.w, i * 3);
      const c0 = g.clearance(a, n);
      const ce = c0 + pt.gw * g.give; // loose cloth: the elbow end may rest pressed into it
      const m = MARGIN[pt.part];
      this.tau[i] = ce >= m + SOFT ? NaN : softTarget(ce, m);
      if (c0 < REACH) cand.push(i);
      before = Math.max(before, -ce);
    }

    // Relax toward the animated pose, then project.
    const x0 = Float32Array.from(this.x);
    decay(this.x, this.v, 0, 4, RELAX, dt);
    const xr = Float32Array.from(this.x);
    for (let it = 0; it < ITERS; it++) {
      const fk = this.#fk(S, E, W, Qp, hinge0, cand);
      if (!this.#project(g, fk, flex0, Qp, cand)) break;
    }
    // Spring velocity: follow the projection when it moved the correction.
    let moved = 0;
    for (let k = 0; k < 4; k++) moved = Math.max(moved, Math.abs(this.x[k] - xr[k]));
    if (moved > 1e-6 && dt > 0) for (let k = 0; k < 4; k++) this.v[k] = Math.max(-6, Math.min(6, (this.x[k] - x0[k]) / dt));
    const fk = this.#fk(S, E, W, Qp, hinge0, cand);
    this.#apply(fk, Qh);
    let after = 0;
    for (const i of cand) after = Math.max(after, -g.clearance(a.fromArray(this.c, i * 3), n) - this.pts[i].gw * g.give);
    const active = Math.hypot(this.x[0], this.x[1], this.x[2]) + Math.abs(this.x[3]) > 1e-4;
    return { before, after, active };
  }

  // One damped Gauss-Newton step over every violating point: the normal equations
  // (J^T J + lambda W) dx = J^T e in [shoulder rotation (world), hinge], with W the joint
  // stiffness (twist and elbow cost more than shoulder swing). Using all points, not the
  // deepest one, keeps the solution continuous as points come and go. False when clear.
  #project(g, fk, flex0, Qp, cand) {
    const a = this.tmp.a, n = this.tmp.n;
    const A = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], b = [0, 0, 0, 0];
    const d = new THREE.Vector3(), js = new THREE.Vector3(), le = new THREE.Vector3();
    let count = 0, hand = false;
    for (const i of cand) {
      a.fromArray(this.c, i * 3);
      const c = g.clearance(a, n) + this.pts[i].gw * g.give;
      const tau = Number.isNaN(this.tau[i]) ? MARGIN[this.pts[i].part] : this.tau[i];
      const e = tau - c;
      if (e <= 0.0003) continue;
      const seg = this.seg[i];
      hand ||= seg === 2 || (seg === 1 && this.pts[i].gw < 0.5); // the wrist half moves the wrist
      const lever = seg === 2 ? fk.W1 : a;
      js.crossVectors(d.subVectors(lever, fk.S), n); // n . (w x d) = w . (d x n)
      const je = seg > 0 ? le.crossVectors(fk.hinge, d.subVectors(lever, fk.E1)).dot(n) : 0;
      const j = [js.x, js.y, js.z, je];
      for (let r = 0; r < 4; r++) {
        b[r] += j[r] * e;
        for (let k = 0; k < 4; k++) A[r * 4 + k] += j[r] * j[k];
      }
      count++;
    }
    if (!count) return false;
    // Only the elbow end is in: keep the wrist where the animation had it, so the elbow
    // swivels / bends out rather than the whole arm swinging away (an A-pose). With the wrist
    // half or the hand in, the wrist has to move, and keeping it would fight the push.
    if (!hand) {
      const dW = new THREE.Vector3().subVectors(this.W0, fk.W1);
      const cols = [0, 1, 2].map((k) => new THREE.Vector3().setComponent(k, 1).cross(d.subVectors(fk.W1, fk.S)));
      cols.push(le.crossVectors(fk.hinge, d.subVectors(fk.W1, fk.E1)).clone());
      for (let r = 0; r < 4; r++) {
        b[r] += KEEP * cols[r].dot(dW);
        for (let k = 0; k < 4; k++) A[r * 4 + k] += KEEP * cols[r].dot(cols[k]);
      }
    }
    const u = new THREE.Vector3().subVectors(fk.E1, fk.S).normalize();
    const lam = DAMP * (1 + count * 0.02);
    for (let r = 0; r < 3; r++) for (let k = 0; k < 3; k++) A[r * 4 + k] += lam * ((r === k ? 1 : 0) + (TWIST_STIFF - 1) * u.getComponent(r) * u.getComponent(k));
    A[15] += lam * ELBOW_STIFF;
    const dx = linsolve([0, 1, 2, 3].map((r) => A.slice(r * 4, r * 4 + 4)), b);
    this.#step(dx, fk, flex0, Qp);
    return true;
  }

  // Corrected chain for the current x: world rotation of the shoulder correction, the elbow
  // hinge, joint positions, and the corrected positions of points `idx` (into this.c).
  #fk(S, E, W, Qp, hinge0, idx) {
    const Rs = qexp(this.x, 0, new THREE.Quaternion()).premultiply(Qp).multiply(Qp.clone().invert()); // parent frame -> world
    const E1 = new THREE.Vector3().subVectors(E, S).applyQuaternion(Rs).add(S);
    const hinge = hinge0.clone().applyQuaternion(Rs);
    const Re = new THREE.Quaternion().setFromAxisAngle(hinge, this.x[3]);
    const Rf = Re.clone().multiply(Rs); // forearm world delta
    const W1 = new THREE.Vector3().subVectors(W, E).applyQuaternion(Rf).add(E1);
    const a = this.tmp.a;
    for (const i of idx) {
      a.fromArray(this.w, i * 3);
      const seg = this.seg[i];
      if (seg === 0) a.sub(S).applyQuaternion(Rs).add(S);
      else if (seg === 1) a.sub(E).applyQuaternion(Rf).add(E1);
      else a.sub(W).add(W1);
      a.toArray(this.c, i * 3);
    }
    return { S, E1, W1, hinge, Rs, Rf };
  }

  // Compose the step into x (shoulder in the parent frame), within the limits.
  #step(dx, fk, flex0, Qp) {
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(dx[0], dx[1], dx[2]).normalize(), Math.hypot(dx[0], dx[1], dx[2]) || 0);
    if (!Math.hypot(dx[0], dx[1], dx[2])) q.identity();
    const local = Qp.clone().invert().multiply(q).multiply(Qp); // world step -> parent frame
    const cur = qexp(this.x, 0, new THREE.Quaternion());
    const next = local.multiply(cur);
    const r = qlog(next, new Float32Array(3));
    const ang = Math.hypot(r[0], r[1], r[2]);
    if (ang > MAX_SWING) for (let k = 0; k < 3; k++) r[k] *= MAX_SWING / ang;
    this.x[0] = r[0], this.x[1] = r[1], this.x[2] = r[2];
    const flex = flex0 + this.flexSign * (this.x[3] + dx[3]);
    const clamped = Math.min(FLEX_MAX, Math.max(Math.min(FLEX_MIN, flex0), flex));
    this.x[3] = (clamped - flex0) * this.flexSign;
  }

  // Write the corrected rotations: upper arm and forearm take the deltas, the hand keeps its
  // world orientation (its local rotation absorbs the forearm's change).
  #apply(fk, Qh) {
    const Qua = this.ua.getWorldQuaternion(new THREE.Quaternion());
    const Qla = this.la.getWorldQuaternion(new THREE.Quaternion());
    const Qua1 = fk.Rs.clone().multiply(Qua);
    const Qla1 = fk.Rf.clone().multiply(Qla);
    const Qpar = this.ua.parent.getWorldQuaternion(new THREE.Quaternion());
    this.ua.quaternion.copy(Qpar.invert().multiply(Qua1));
    this.la.quaternion.copy(Qua1.clone().invert().multiply(Qla1));
    this.hand.quaternion.copy(Qla1.clone().invert().multiply(Qh));
    this.ua.updateMatrixWorld(true);
  }
}

// Signed angle from a to b about axis (projected onto the plane normal to axis).
function signedAngle(a, b, axis) {
  const pa = a.clone().projectOnPlane(axis), pb = b.clone().projectOnPlane(axis);
  return Math.atan2(new THREE.Vector3().crossVectors(pa, pb).dot(axis), pa.dot(pb));
}

// Small dense solve (Gaussian elimination with partial pivoting).
function linsolve(A, y) {
  const n = y.length;
  const M = A.map((r, i) => [...r, y[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    const d = M[c][c] || 1e-12;
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / d;
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((r, i) => r[n] / (r[i] || 1e-12));
}
