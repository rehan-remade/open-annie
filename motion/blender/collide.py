"""Mesh-accurate collision QA for Annie's clips, on the *deformed* avatar mesh in Blender.

Every frame the armature-deformed Body / Face / Hair meshes are evaluated (the MToon outline node
modifiers are switched off, so the vertex order matches the base mesh) and split into parts by each
vertex's dominant bone weight:

  moving  : per side, the forearm (sleeve + skin), hand and finger vertices, plus the elbow end of
            the upper-arm sleeve, subsampled on a ~8 mm grid, in three classes: hand (bare skin,
            fingers), forearm (sleeve, a >= 0.62) and elbow region (a < 0.62).
  torso   : hips, spine, chest, upperChest, neck, shoulders, bust / hood secondary bones: skin,
            camisole, cardigan and shorts together.
  head    : face, hair and head-weighted body vertices (rigid with the head: built once, queried
            in head space).
  legs    : each thigh + shin.
  hands   : the other hand's skin (hand-vs-hand: claps, clasped hands).

Penetration test (robust to the layered, open clothing): torso, legs and head are star-shaped around
an axis (the spine polyline, each leg's bone chain, the head centre). A moving point p is inside a
part when a ray from its closest axis point c through p still hits that part's surface beyond p;
the depth is the distance from p to the *outermost* hit (the cardigan, not the skin under it), and
the push direction is the ray direction. Hands use closest point + face normal on the other hand.

`Collider.measure()` returns per-side depth, the part it is inside, where on the arm (0 = elbow,
1 = hand) and the push vector; export.py turns those into IK-target and elbow-pole corrections.
"""

import re

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

import rig

SIDE_LR = {"left": "L", "right": "R"}
FINGER_NAMES = ("Thumb", "Index", "Middle", "Ring", "Little")


def _is_torso(g):
    """Torso, clothes on it, and the secondary (spring) bones of bust, hood, jacket hem and skirt
    (minus any spring the avatar manifest hides, e.g. the jacket's CoatSkirt)."""
    g = g or ""
    hidden = rig.HIDE.get("springs")
    if hidden and re.search(hidden, g):
        return False
    return g in {"J_Bip_C_Hips", "J_Bip_C_Spine", "J_Bip_C_Chest", "J_Bip_C_UpperChest", "J_Bip_C_Neck",
                 "J_Bip_L_Shoulder", "J_Bip_R_Shoulder"} or (g.startswith("J_Sec_") and any(
                     k in g for k in ("Bust", "Hood", "Skirt", "Coat")))


def visible_faces(obj):
    """Per polygon: does any corner / the centre sample the base-colour texture at alpha >= 0.5?

    VRoid outfits are alpha-masked (MASK, cutoff 0.5) and keep invisible geometry (e.g. the unused
    long coat skirt around the shins): only what renders may count as an obstacle."""
    me = obj.data
    n = len(me.polygons)
    vis = np.ones(n, dtype=bool)
    uv = me.uv_layers.active
    if uv is None:
        return vis
    uvs = np.empty(len(me.loops) * 2)
    uv.data.foreach_get("uv", uvs)
    uvs = uvs.reshape(-1, 2)
    starts = np.empty(n, dtype=int)
    counts = np.empty(n, dtype=int)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", counts)
    mats = np.empty(n, dtype=int)
    me.polygons.foreach_get("material_index", mats)
    for mi, slot in enumerate(obj.material_slots):
        m = slot.material
        img = None
        try:
            src = m.vrm_addon_extension.mtoon1.pbr_metallic_roughness.base_color_texture.index.source
            img = src if src and src.channels == 4 else None
            mode = m.vrm_addon_extension.mtoon1.alpha_mode
        except Exception:
            mode = "OPAQUE"
        sel = np.nonzero(mats == mi)[0]
        if img is None or mode != "MASK" or not len(sel):
            continue
        W, H = img.size
        px = np.empty(W * H * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        alpha = px[3::4].reshape(H, W)

        def a_at(u, v):
            x = np.clip((np.mod(u, 1.0) * W).astype(int), 0, W - 1)
            y = np.clip((np.mod(v, 1.0) * H).astype(int), 0, H - 1)
            return alpha[y, x]

        for i in sel:
            L = uvs[starts[i]:starts[i] + counts[i]]
            c = L.mean(0)
            pts = np.vstack([L, c, (L + c) / 2])
            vis[i] = bool((a_at(pts[:, 0], pts[:, 1]) >= 0.5).any())
    return vis


def _is_hand(g, S):
    return g == f"J_Bip_{S}_Hand" or any(g == f"J_Bip_{S}_{f}{i}" for f in FINGER_NAMES for i in (1, 2, 3))


def _grid_sample(P, cell):
    """Indices of one point per occupied grid cell (keeps the sample even and small)."""
    key = np.floor(P / cell).astype(np.int64)
    _, idx = np.unique(key, axis=0, return_index=True)
    return np.sort(idx)


def _closest_on_polyline(A, P):
    """A: (k,3) polyline, P: (n,3). Returns closest points C (n,3) and segment index."""
    best = np.full(len(P), np.inf)
    C = np.zeros_like(P)
    seg = np.zeros(len(P), dtype=int)
    for i in range(len(A) - 1):
        a, b = A[i], A[i + 1]
        ab = b - a
        t = np.clip(((P - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
        Q = a + t[:, None] * ab
        d = np.einsum("ij,ij->i", P - Q, P - Q)
        m = d < best
        best[m], C[m], seg[m] = d[m], Q[m], i
    return C, seg


class Collider:
    def __init__(self, arm, cell=0.008):
        self.arm = arm
        self.body = bpy.data.objects["Body"]
        self.face = bpy.data.objects["Face"]
        self.hair = next(o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith("Hair"))
        for o in (self.body, self.face, self.hair):
            for md in o.modifiers:
                if md.type == "NODES":
                    md.show_viewport = False
        me = self.body.data
        gname = {g.index: g.name for g in self.body.vertex_groups}
        self.dom = []
        for v in me.vertices:
            self.dom.append(gname[max(v.groups, key=lambda x: x.weight).group] if v.groups else None)
        vis = visible_faces(self.body)
        self.visible_ratio = float(vis.mean())
        polys = [tuple(p.vertices) for p, ok in zip(me.polygons, vis) if ok]
        self.tris = {}
        tri = []
        for p in polys:
            for k in range(1, len(p) - 1):
                tri.append((p[0], p[k], p[k + 1]))
        tri = np.array(tri)
        seen = np.zeros(len(me.vertices), dtype=bool)
        seen[tri.ravel()] = True  # vertices of visible faces (moving points must render too)
        dom = np.array([d or "" for d in self.dom])

        def faces_where(pred):
            ok = np.array([pred(d) for d in dom])
            return tri[ok[tri].all(1)]

        self.tris["torso"] = faces_where(_is_torso)
        for s, S in SIDE_LR.items():
            self.tris[f"leg_{S}"] = faces_where(lambda d, S=S: d in (f"J_Bip_{S}_UpperLeg", f"J_Bip_{S}_LowerLeg"))
            self.tris[f"hand_{S}"] = faces_where(lambda d, S=S: _is_hand(d, S))
        self.tris_l = {k: v.tolist() for k, v in self.tris.items()}
        # moving points: forearm / hand / fingers (+ the elbow end of the upper-arm sleeve)
        rest = self._world_co(self.body, evaluated=False)
        P = self.arm.pose.bones
        self.moving = {}
        for s, S in SIDE_LR.items():
            e = P[rig.VRM[f"{s}LowerArm"]].bone
            eh, et = e.head_local, e.tail_local
            u = P[rig.VRM[f"{s}UpperArm"]].bone
            idx, wgt = [], []
            for i, d in enumerate(self.dom):
                if d is None or not seen[i]:
                    continue
                v = Vector(rest[i])
                # wgt = arm fraction a: 0 at the shoulder, 0.5 at the elbow, 1 at the hand
                if _is_hand(d, S):
                    idx.append(i)
                    wgt.append(1.0)
                elif d == f"J_Bip_{S}_LowerArm":
                    t = (v - eh).dot(et - eh) / (et - eh).length_squared
                    idx.append(i)
                    wgt.append(0.5 + 0.5 * float(min(1, max(0, t))))
                elif d == f"J_Bip_{S}_UpperArm":
                    t = (v - u.head_local).dot(u.tail_local - u.head_local) / (u.tail_local - u.head_local).length_squared
                    if t > 0.72:  # the elbow end; nearer the armpit the sleeve is inside the armhole by design
                        idx.append(i)
                        wgt.append(0.5 * float(min(1, t)))
            idx, wgt = np.array(idx), np.array(wgt)
            keep = _grid_sample(rest[idx], cell)
            self.moving[S] = (idx[keep], wgt[keep])
        # head: rigid with the head bone, built once in rest space
        hb = P[rig.VRM["head"]].bone
        self.head_rest = hb.head_local.copy()
        self.head_center = hb.head_local + Vector((0, -0.01, 0.075))  # between the ears (Blender: -y = forward)
        hv, ht = [], []
        for o in (self.face, self.hair, self.body):
            co = self._world_co(o, evaluated=False)
            if o is self.body:
                keep = np.array([d == "J_Bip_C_Head" for d in dom])
                tr = tri[keep[tri].all(1)]
            else:
                # face, and the hair that moves rigidly with the head; long spring-boned strands
                # (twin-tails) are pushed around by her arms at runtime, so they are not obstacles
                gn = {g.index: g.name for g in o.vertex_groups}
                hd = np.array([gn[max(v.groups, key=lambda x: x.weight).group] == "J_Bip_C_Head" if v.groups else False
                               for v in o.data.vertices])
                ovis = visible_faces(o)
                tr = []
                for p, ok in zip(o.data.polygons, ovis):
                    if ok and hd[list(p.vertices)].all():
                        for k in range(1, len(p.vertices) - 1):
                            tr.append((p.vertices[0], p.vertices[k], p.vertices[k + 1]))
                tr = np.array(tr).reshape(-1, 3)
            base = sum(len(x) for x in hv)
            hv.append(co)
            ht.append(tr + base)
        self.head_bvh = BVHTree.FromPolygons(np.concatenate(hv).tolist(), np.concatenate(ht).tolist(), all_triangles=True)
        self.axis_names = ["hips", "spine", "chest", "upperChest", "neck", "head"]
        tv = rest[np.unique(self.tris["torso"].ravel())]
        self.torso_drop = float(P[rig.VRM["hips"]].bone.head_local.z - tv[:, 2].min()) + 0.02  # hips -> below the skirt hem

    def _world_co(self, obj, evaluated=True):
        if evaluated:
            dg = bpy.context.evaluated_depsgraph_get()
            ob = obj.evaluated_get(dg)
            me = ob.to_mesh()
            n = len(me.vertices)
            co = np.empty(n * 3)
            me.vertices.foreach_get("co", co)
            M = np.array(ob.matrix_world)
            ob.to_mesh_clear()
        else:
            me = obj.data
            co = np.empty(len(me.vertices) * 3)
            me.vertices.foreach_get("co", co)
            M = np.array(obj.matrix_world)
        co = co.reshape(-1, 3)
        return co @ M[:3, :3].T + M[:3, 3]

    @staticmethod
    def _outermost(bvh, origin, direction, limit):
        """Distance to the farthest surface hit along a ray (0 if none)."""
        far, o, left = 0.0, Vector(origin), limit
        for _ in range(12):
            loc, _n, _i, d = bvh.ray_cast(o, direction, left)
            if loc is None:
                break
            far = (Vector(loc) - Vector(origin)).length
            o = Vector(loc) + direction * 1e-4
            left = limit - far
            if left <= 0:
                break
        return far

    def _radial(self, bvh, axis, Pts, rmax):
        """Star-shaped inside test. Returns depth (n,), push dirs (n,3)."""
        C, _ = _closest_on_polyline(axis, Pts)
        D = Pts - C
        r = np.linalg.norm(D, axis=1)
        depth = np.zeros(len(Pts))
        dirs = np.zeros_like(Pts)
        self._gap = np.full(len(Pts), np.inf)  # signed clearance (m) where the ray finds the part
        for i in np.nonzero((r < rmax) & (r > 1e-6))[0]:
            d = Vector(D[i] / r[i])
            R = self._outermost(bvh, C[i], d, rmax + 0.05)
            if R > 0:
                self._gap[i] = r[i] - R
            if R > r[i]:
                depth[i] = R - r[i]
                dirs[i] = D[i] / r[i]
        return depth, dirs

    def measure(self, margin=0.0):
        """Penetration of each arm into the body at the current frame.

        Returns {S: {"depth", "part", "w", "push_hand", "push_elbow", "pts", "sd"...}}."""
        Pb = self.arm.pose.bones
        J = {n: np.array(Pb[rig.VRM[n]].head) for n in rig.VRM}
        co = self._world_co(self.body)
        out = {}
        bvh = {}
        tv = co.tolist()
        for k in ("torso", "leg_L", "leg_R", "hand_L", "hand_R"):
            bvh[k] = BVHTree.FromPolygons(tv, self.tris_l[k], all_triangles=True)
        hips = J["hips"]
        down = hips - (J["spine"] - hips) / max(np.linalg.norm(J["spine"] - hips), 1e-6) * self.torso_drop
        torso_axis = np.array([down] + [J[n] for n in self.axis_names])
        # head space transform
        hpb = Pb[rig.VRM["head"]]
        Rh = np.array(hpb.matrix.to_3x3() @ hpb.bone.matrix_local.to_3x3().transposed())
        hpos = np.array(hpb.head)
        hand_c = {S: co[self.moving[S][0][self.moving[S][1] >= 0.99]].mean(0) for S in ("L", "R")}
        for s, S in SIDE_LR.items():
            idx, w = self.moving[S]
            Pts = co[idx]
            res = []
            dt, dirt = self._radial(bvh["torso"], torso_axis, Pts, 0.32)
            gaps = [self._gap]
            res.append(("torso", dt, dirt))
            for L in ("L", "R"):
                sl = "left" if L == "L" else "right"
                leg_axis = np.array([J[f"{sl}UpperLeg"], J[f"{sl}LowerLeg"], J[f"{sl}Foot"]])
                dl, dirl = self._radial(bvh[f"leg_{L}"], leg_axis, Pts, 0.16)
                gaps.append(self._gap)
                res.append((f"leg_{L}", dl, dirl))
            gap = np.min(gaps, axis=0)  # nearest body clearance per arm point (torso, skirt, thighs)
            # head: query in head rest space
            Q = (Pts - hpos) @ Rh + np.array(self.head_rest)
            hc = np.array(self.head_center)
            Dq = Q - hc
            rq = np.linalg.norm(Dq, axis=1)
            dh = np.zeros(len(Pts))
            dirh = np.zeros_like(Pts)
            for i in np.nonzero(rq < 0.2)[0]:
                d = Vector(Dq[i] / rq[i])
                R = self._outermost(self.head_bvh, hc, d, 0.25)
                if R > rq[i]:
                    dh[i] = R - rq[i]
                    dirh[i] = Rh @ (Dq[i] / rq[i])
            res.append(("head", dh, dirh))
            # the other hand (skin only): closest point + normal
            O = "R" if S == "L" else "L"
            do = np.zeros(len(Pts))
            diro = np.zeros_like(Pts)
            for i in (np.nonzero(w >= 0.99)[0] if getattr(self, "hand_vs_hand", True) else []):
                loc, nrm, _, dist = bvh[f"hand_{O}"].find_nearest(Vector(Pts[i]), 0.03)
                if loc is None:
                    continue
                v = Vector(Pts[i]) - loc
                if v.dot(nrm) < 0:
                    do[i] = dist
                    # separate along the line between the two hands (stable for claps and clasps)
                    sep = hand_c[S] - hand_c[O]
                    diro[i] = sep / max(np.linalg.norm(sep), 1e-6)
            res.append(("hand", do, diro))
            depth = np.max([r[1] for r in res], axis=0)
            which = np.argmax([r[1] for r in res], axis=0)
            dirs = np.array([res[k][2][i] for i, k in enumerate(which)]) if len(Pts) else np.zeros((0, 3))
            parts = [res[k][0] for k in which]
            if getattr(self, "debug", False):
                self.dbg = getattr(self, "dbg", [])
                for k in np.nonzero(depth > 0.002)[0]:
                    self.dbg.append([*map(float, Pts[k]), float(depth[k]), parts[k], float(w[k])])
            pen = depth > 0
            cls = np.where(w >= 0.99, 0, np.where(w >= 0.62, 1, 2))  # 0 hand/fingers, 1 forearm, 2 elbow region
            i = int(np.argmax(depth)) if len(depth) else 0
            mx = lambda c: float(depth[cls == c].max()) if (cls == c).any() else 0.0  # noqa: E731
            out[S] = {"depth": float(depth.max()) if len(depth) else 0.0, "part": parts[i] if len(depth) else None,
                      "a": float(w[i]) if len(depth) else 0.0,
                      "hand": mx(0), "forearm": mx(1), "elbow": mx(2),
                      "gap_hand": float(gap[cls <= 1].min()) if (cls <= 1).any() else np.inf,
                      "gap_elbow": float(gap[cls == 2].min()) if (cls == 2).any() else np.inf,
                      "pen_depth": depth[pen], "pen_dir": dirs[pen], "pen_a": w[pen], "pen_part": [p for p, q in zip(parts, pen) if q],
                      "pen_pos": Pts[pen],
                      "joints": {k: np.array(Pb[rig.VRM[f"{s}{k}"]].head) for k in ("UpperArm", "LowerArm", "Hand")},
                      "pole": np.array(Pb[f"CTRL_elbow_{S}"].head)}
        return out

    def contact_gap(self, S, part, anchor="hand"):
        """Signed gap (m, + = outside) from the hand's nearest point to a part, with the push direction.

        Intentional contacts touch with whatever part of the hand is nearest (the authored pose decides
        which: palm on the chest, fingertip on the chin), so no other point of the hand sinks in."""
        Pb = self.arm.pose.bones
        co = self._world_co(self.body)
        idx, w = self.moving[S]
        Pts = co[idx[w >= 0.99]]
        J = {n: np.array(Pb[rig.VRM[n]].head) for n in rig.VRM}
        if part == "torso":
            bvh = BVHTree.FromPolygons(co.tolist(), self.tris_l["torso"], all_triangles=True)
            hips = J["hips"]
            down = hips - (J["spine"] - hips) / np.linalg.norm(J["spine"] - hips) * self.torso_drop
            axis = np.array([down] + [J[n] for n in self.axis_names])
            C, _ = _closest_on_polyline(axis, Pts)
            D = Pts - C
            r = np.linalg.norm(D, axis=1)
            up = Vector(axis[-2] - axis[1]).normalized()
            chest_y = float(J["chest"] @ np.array(up)) - 0.02
            gaps = []
            for i in np.argsort(r)[:40]:  # the nearest-to-axis points are the candidates
                d = Vector(D[i] / r[i])
                cone = (Vector((0, 0, 0)),)
                if float(C[i] @ np.array(up)) > chest_y:
                    # on the chest: the outermost surface over a small cone (+-6 deg), so the necklace's heart
                    # pendant counts as the surface the hand rests on (the runtime's polar height maps, 5.6 deg
                    # bins, see it that way)
                    side = d.cross(up).normalized()
                    cone = (Vector((0, 0, 0)), side, -side, up, -up)
                R = max(self._outermost(bvh, C[i], (d + e * 0.105).normalized(), 0.45) for e in cone)
                gaps.append((r[i] - R, D[i] / r[i]))
        elif part == "head":
            hpb = Pb[rig.VRM["head"]]
            Rh = np.array(hpb.matrix.to_3x3() @ hpb.bone.matrix_local.to_3x3().transposed())
            Q = (Pts - np.array(hpb.head)) @ Rh + np.array(self.head_rest)
            hc = np.array(self.head_center)
            D = Q - hc
            r = np.linalg.norm(D, axis=1)
            gaps = []
            for i in np.argsort(r)[:40]:
                R = self._outermost(self.head_bvh, hc, Vector(D[i] / r[i]), 0.3)
                gaps.append((r[i] - R, Rh @ (D[i] / r[i])))
        else:
            raise ValueError(part)
        g, d = min(gaps, key=lambda x: x[0])
        return float(g), np.array(d)
