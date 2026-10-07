"""Collision auto-fix: push the arm IK targets out of the body, temporally smoothed, then re-measure.

For each clip (after animate.apply_clip has keyed the authored controls):

  1. measure every frame on the deformed mesh (collide.Collider): penetrating arm points, their depth,
     push direction (out of the part they are inside) and arm fraction a (0.5 elbow .. 1 hand);
  2. per frame, the wrist needs to move by the largest  dir * (depth + margin) / a  (a straight arm
     swings about the shoulder, so a point at the elbow needs twice the wrist travel), and the elbow
     pole by  K * dir * (depth + margin)  for points near the elbow (bent arms swing the elbow out);
     hand-vs-hand contacts push each hand half the depth;
  3. those per-frame vectors are dilated (max magnitude over +-3 frames) and Gaussian-smoothed, so the
     correction eases in before a contact and out after it instead of jittering, and accumulated into
     dense keys on the FIX_hand_* / FIX_elbow_* bones (parents of the animator's controls);
  4. repeat until nothing penetrates by more than the tolerance (or the iteration budget runs out).

Intentional contacts (`contacts` on a clip: hand to chest, finger to chin, palms in a clap) are
pulled / pushed onto the surface at `gap`, so they touch instead of hovering or sinking in.
Non-loop clips keep their first and last frames untouched (they are the idle base pose, solved
once as a static pose), loops are smoothed circularly.
"""

import math

import bpy
import numpy as np


SIDES = ("L", "R")
TOL = 0.004  # hands, fingers, forearms: resolve to within 4 mm
MARGIN_HAND = 0.004  # pushed-out hands end up this far off the surface (reads as touching)
MARGIN_FOREARM = 0.004
# Jacketless Annie: bare arms against skin, the crop top and the skirt. Skin is firm, so the elbow
# end gets only a sliver of give (the jacket version allowed 30 mm of cloth-on-cloth compression).
ELBOW_ALLOW = 0.005
WRIST_CAP = 0.08  # never move a wrist target further than this from where it was animated
# During an intentional contact (palm on the chest, finger to the chin) the wrist may just touch.
CUFF_ALLOW = 0.005


def _gauss(x, sigma, loop):
    if sigma <= 0:
        return x
    r = int(math.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    n = len(x)
    out = np.zeros_like(x)
    for j, kv in enumerate(k):
        o = j - r
        if loop:
            out += kv * np.roll(x, -o, axis=0)
        else:
            idx = np.clip(np.arange(n) + o, 0, n - 1)
            out += kv * x[idx]
    return out


def envelope(v, loop, reach=3, sigma=2.0):
    """Dilate (keep the largest vector within +-reach frames), then smooth: no jitter, eases in/out."""
    n = len(v)
    mag = np.linalg.norm(v, axis=1)
    d = np.zeros_like(v)
    for f in range(n):
        idx = np.arange(f - reach, f + reach + 1)
        idx = idx % n if loop else idx[(idx >= 0) & (idx < n)]
        j = idx[int(np.argmax(mag[idx]))]
        d[f] = v[j]
    return _gauss(d, sigma, loop)


class Solver:
    def __init__(self, arm, col, fps=30):
        self.arm = arm
        self.col = col
        self.fps = fps

    # ------------------------------------------------------------------ dense FIX keys
    def _fcurves(self, bone):
        from bpy_extras.anim_utils import action_ensure_channelbag_for_slot
        ad = self.arm.animation_data
        cb = action_ensure_channelbag_for_slot(ad.action, ad.action_slot)
        path = f'pose.bones["{bone}"].location'
        fcs = []
        for i in range(3):
            fc = cb.fcurves.find(path, index=i) or cb.fcurves.new(path, index=i)
            fcs.append(fc)
        return fcs

    def write(self, bone, local):
        """local: (n, 3) per-frame location of a FIX bone (its own rest-local axes)."""
        n = len(local)
        for i, fc in enumerate(self._fcurves(bone)):
            fc.keyframe_points.clear()
            fc.keyframe_points.add(n)
            co = np.zeros((n, 2))
            co[:, 0] = np.arange(n)
            co[:, 1] = local[:, i]
            fc.keyframe_points.foreach_set("co", co.ravel())
            fc.keyframe_points.foreach_set("interpolation", [bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["LINEAR"].value] * n)
            fc.update()

    # ------------------------------------------------------------------ passes
    def measure(self, n, frames=None, contacts=()):
        """Measure frames; also the FIX bones' world rotation per frame (to map world pushes to local)."""
        P = self.arm.pose.bones
        res = []
        for f in (frames if frames is not None else range(n)):
            bpy.context.scene.frame_set(f)
            m = self.col.measure()
            m["R_fix"] = {f"{k}_{S}": np.array(P[f"FIX_{k}_{S}"].matrix.to_3x3()) for k in ("hand", "elbow") for S in SIDES}
            m["contact"] = {}
            for c in contacts:
                S, t0, t1, part, anchor, gap = c
                if t0 - 0.25 <= f / self.fps <= t1 + 0.25:
                    m["contact"][S] = (self.col.contact_gap(S, part), gap, t0, t1)
            m["frame"] = f
            m["in_contact"] = {S: any(c[0] == S and c[1] <= f / self.fps <= c[2] for c in contacts) for S in SIDES}
            res.append(m)
        return res

    def _write_all(self, corr, m):
        for k, v in corr.items():
            R = np.array([x["R_fix"][k] for x in m])
            self.write(f"FIX_{k}", np.einsum("fji,fj->fi", R, v))  # R^T v per frame

    def solve(self, clip, n, iters=10, attach_iters=6, log=print):
        loop = bool(clip.get("loop"))
        contacts = clip.get("contacts", ())
        self.col.hand_vs_hand = not clip.get("hands_touch")  # clasped hands: skip hand-vs-hand
        corr = {f"{k}_{S}": np.zeros((n, 3)) for k in ("hand", "elbow") for S in SIDES}
        history = []
        m0 = None
        ramp = np.clip(np.minimum(np.arange(n), np.arange(n)[::-1]) / 4.0, 0, 1)[:, None]

        def window(x, S, f):
            c = x["contact"].get(S)
            if c is None:
                return None, 0.0
            t = f / self.fps
            return c, min(1.0, max(0.0, min(t - (c[2] - 0.25), (c[3] + 0.25) - t) / 0.25))

        def attach(iters, pull_only=False):
            """Intentional contacts: the nearest point of the hand at `gap` from the surface."""
            nonlocal m0
            for it in range(iters if contacts else 0):
                m = self.measure(n, contacts=contacts)
                m0 = m0 or m
                push = np.zeros((n, 3))
                err = 0.0
                for S in SIDES:
                    push[:] = 0
                    for f, x in enumerate(m):
                        c, wgt = window(x, S, f)
                        if c is None or wgt <= 0:
                            continue
                        (g, gdir), gap = c[0], c[1]
                        d = g - gap
                        if pull_only and (d <= 0 or not x["in_contact"][S]):
                            continue
                        push[f] = -gdir * float(np.clip(d, -0.03, 0.03)) * 0.7 * wgt
                        if wgt >= 1:
                            err = max(err, abs(d))
                    corr[f"hand_{S}"] += _gauss(push, 1.0 if pull_only else 1.5, loop) * (1 if loop else ramp)
                self._write_all(corr, m)
                if err <= 0.003:
                    break

        attach(attach_iters)
        # 2. resolve penetration: push out only, never pull in (keep the best iterate: the passes are
        # coupled through the smoothing and the arm chain, so the last one is not always the best)
        best = None
        for it in range(iters + 1):
            m = self.measure(n, contacts=contacts)
            m0 = m0 or m
            h = max(max(x[S]["hand"] for S in SIDES) for x in m)
            fa = max(max(_fa(x, S) for S in SIDES) for x in m)
            el = max(max(x[S]["elbow"] for S in SIDES) for x in m)
            cm = max((abs(x["contact"][S][0][0] - x["contact"][S][1]) for x in m for S in x["contact"]
                      if x["contact"][S][2] <= x["frame"] / self.fps <= x["contact"][S][3]), default=0)
            history.append(tuple(round(v * 1000, 1) for v in (h, fa, el, cm)))
            score = max(h, fa) + 0.1 * max(0.0, el - ELBOW_ALLOW)
            if best is None or score < best[0] - 1e-5:
                best = (score, {k: v.copy() for k, v in corr.items()}, m)
            if it == iters or (h <= TOL and fa <= TOL and el <= ELBOW_ALLOW + TOL):
                if score > best[0] + 1e-5:  # fall back to the best iterate
                    corr = best[1]
                    self._write_all(corr, best[2])
                    m = self.measure(n, contacts=contacts)
                    h = max(max(x[S]["hand"] for S in SIDES) for x in m)
                    fa = max(max(_fa(x, S) for S in SIDES) for x in m)
                    el = max(max(x[S]["elbow"] for S in SIDES) for x in m)
                    history.append(tuple(round(v * 1000, 1) for v in (h, fa, el, cm)))
                break
            push = {k: np.zeros((n, 3)) for k in corr}
            for f, x in enumerate(m):
                for S in SIDES:
                    c, wgt = window(x, S, f)
                    touching = bool(x.get("in_contact", {}).get(S))  # the core window, as reported
                    wp, ep = pushes(x[S], margin_hand=c[1] if c is not None and wgt > 0 else MARGIN_HAND,
                                    forearm_allow=CUFF_ALLOW if touching else 0.0)
                    push[f"hand_{S}"][f] = wp
                    push[f"elbow_{S}"][f] = ep
            for k in corr:
                step = envelope(push[k], loop)
                corr[k] += 0.85 * (step if loop else step * ramp)  # keep the base-pose ends exact
                if k.startswith("hand"):
                    mag = np.linalg.norm(corr[k], axis=1, keepdims=True)
                    corr[k] *= np.minimum(1.0, WRIST_CAP / np.maximum(mag, 1e-9))
            self._write_all(corr, m)
        if contacts:  # 3. settle the contacts back onto the surface, then one last push-out pass
            attach(3, pull_only=True)
            for it in range(2):
                m = self.measure(n, contacts=contacts)
                push = {k: np.zeros((n, 3)) for k in corr}
                for f, x in enumerate(m):
                    for S in SIDES:
                        c, wgt = window(x, S, f)
                        touching = bool(x.get("in_contact", {}).get(S))
                        wp, ep = pushes(x[S], margin_hand=c[1] if c is not None and wgt > 0 else MARGIN_HAND,
                                        forearm_allow=CUFF_ALLOW if touching else 0.0)
                        push[f"hand_{S}"][f] = wp
                        push[f"elbow_{S}"][f] = ep
                if not any(v.any() for v in push.values()):
                    break
                for k in corr:
                    step = _gauss(push[k], 1.0, loop)
                    corr[k] += step if loop else step * ramp
                self._write_all(corr, m)
            m = self.measure(n, contacts=contacts)
            h = max(max(x[S]["hand"] for S in SIDES) for x in m)
            fa = max(max(_fa(x, S) for S in SIDES) for x in m)
            el = max(max(x[S]["elbow"] for S in SIDES) for x in m)
            cm = max((abs(x["contact"][S][0][0] - x["contact"][S][1]) for x in m for S in x["contact"]
                      if x["contact"][S][2] <= x["frame"] / self.fps <= x["contact"][S][3]), default=0)
            history.append(tuple(round(v * 1000, 1) for v in (h, fa, el, cm)))
        log(f"  collide {clip['name']}: " + " -> ".join("/".join(str(v) for v in hh) for hh in history)
            + " mm (hand/forearm/elbow/contact)")
        return history, m0, m


def _fa(x, S):
    """Forearm depth that counts: minus the cuff allowance while that hand is in an intentional contact."""
    return max(0.0, x[S]["forearm"] - CUFF_ALLOW) if x.get("in_contact", {}).get(S) else x[S]["forearm"]


def _rot(v, axis, ang):
    axis = axis / np.linalg.norm(axis)
    return v * math.cos(ang) + np.cross(axis, v) * math.sin(ang) + axis * (axis @ v) * (1 - math.cos(ang))


def pushes(r, margin_hand=None, forearm_allow=0.0):
    """Wrist-target push and elbow-pole push (world, m) that resolve one side's penetration."""
    MARGIN_HAND = margin_hand if margin_hand is not None else globals()["MARGIN_HAND"]
    wp, wpm = np.zeros(3), 0.0
    delta_e, dem = np.zeros(3), 0.0
    for d, v, a, part in zip(r["pen_depth"], r["pen_dir"], r["pen_a"], r["pen_part"]):
        cand_w = cand_e = None
        if part == "hand":  # hand vs the other hand: each side takes half
            cand_w = v * (0.5 * d + 0.5 * MARGIN_HAND)
        elif a >= 0.99:
            cand_w = v * (d + MARGIN_HAND)
        elif a >= 0.62:  # forearm: move the wrist and/or the elbow depending on where along it
            if d <= forearm_allow:
                continue
            need = v * (d - forearm_allow + MARGIN_FOREARM)
            sfa = (a - 0.5) / 0.5
            if forearm_allow > 0:  # hand is in an intentional contact: swing the elbow, keep the hand put
                cand_e = need
            else:
                cand_w = need * min(1.0, sfa / 0.25)
                cand_e = need * min(1.0, (1 - sfa) / 0.4)
        elif d > ELBOW_ALLOW:  # sleeve pressing into the jacket's flank: only beyond the allowance, and
            # only by swinging the elbow (pushing the wrist out to clear it splays the arms)
            cand_e = v * (d - ELBOW_ALLOW + 0.003)
        if cand_w is not None and np.linalg.norm(cand_w) > wpm:
            wp, wpm = cand_w, float(np.linalg.norm(cand_w))
        if cand_e is not None and np.linalg.norm(cand_e) > dem:
            delta_e, dem = cand_e, float(np.linalg.norm(cand_e))
    ep = np.zeros(3)
    if dem > 0:
        sh, el, wr = r["joints"]["UpperArm"], r["joints"]["LowerArm"], r["joints"]["Hand"]
        pole = r["pole"]
        ax = wr - sh
        L = np.linalg.norm(ax)
        ax = ax / L
        c = sh + ax * ((el - sh) @ ax)
        e_off = el - c
        de = np.linalg.norm(e_off)
        if de >= 0.05:  # a nearly straight arm can't swing its elbow: leave it (the allowance covers it)
            t_hat = np.cross(ax, e_off / de)
            ang = float(np.clip((delta_e @ t_hat) / de, -0.2, 0.2))
            cp = sh + ax * ((pole - sh) @ ax)
            ep = _rot(pole - cp, ax, ang) + cp - pole
    return wp, ep


def summarize(m):
    """Per-clip numbers from a measure pass (per-frame worst over both arms)."""
    def per(k):
        return np.array([[x[S][k] for S in SIDES] for x in m]).max(1)
    h, el = per("hand"), per("elbow")
    fa = np.array([[_fa(x, S) for S in SIDES] for x in m]).max(1)
    cuff = np.array([[x[S]["forearm"] if x.get("in_contact", {}).get(S) else 0.0 for S in SIDES] for x in m]).max(1)
    hf = np.maximum(h, fa)
    wf = int(np.argmax(hf)) if hf.max() > 0 else int(np.argmax(el))
    worst = {S: (round(m[wf][S]["depth"] * 1000, 1), m[wf][S]["part"], round(m[wf][S]["a"], 2)) for S in SIDES}
    mm = lambda a: round(float(a.max()) * 1000, 1)  # noqa: E731
    gh = np.array([min(x[S].get("gap_hand", np.inf) for S in SIDES) for x in m])
    ge = np.array([min(x[S].get("gap_elbow", np.inf) for S in SIDES) for x in m])
    med = lambda a: round(float(np.median(np.clip(a, -1, 1))) * 1000, 1) if np.isfinite(a).any() else None  # noqa: E731
    return {"max_mm": mm(hf), "mean_mm": round(float(hf.mean()) * 1000, 2), "frames_gt5mm": int((hf > 0.005).sum()),
            "hand_max_mm": mm(h), "forearm_max_mm": mm(fa), "elbow_max_mm": mm(el), "cuff_in_contact_max_mm": mm(cuff),
            "elbow_frames_over_allowance": int((el > ELBOW_ALLOW + 0.005).sum()), "worst_frame": wf, "worst": worst,
            "gap_hand_median_mm": med(gh), "gap_elbow_median_mm": med(ge)}
