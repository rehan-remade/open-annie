"""Build the rig, animate every clip, fix collisions, bake to the VRM humanoid bones, write the pack.

  blender -b -y --python motion/blender/export.py -- [--only wave,clap] [--out DIR] [--no-fix] [--qa]

Per clip: animate.apply_clip keys the controls, fix.Solver pushes the arms out of the deformed mesh
(collide.py) and re-measures, then the rig is baked. Clip JSONs go to assets/clips/annie-blender/
(or --out DIR), collision numbers to motion/raw/blender/stats/<clip>.json. A full run (no --only,
no --out) also writes pack.json (pack.py); `motion/blender/build.sh` runs the clips in parallel
Blender workers and then pack.py.
Needs the VRM Add-on for Blender installed as an extension (see motion/README.md, "Blender rig").

Conversion (verified numerically by --qa, which re-runs three-vrm's FK on the output):
for each humanoid bone, the world-space rotation delta from rest D = R_pose R_rest^T (Blender
world, independent of Blender's bone-roll conventions) is taken to VRM 1.0 space (x = her left,
y = up, z = forward: Blender (x, y, z) -> (x, z, -y)), and the normalized local rotation is
D_parent^T D along the VRM humanoid hierarchy (three-vrm's normalized bones have identity rest and
world-aligned frames, so this is exactly what they compose back to). hips_position is the hips
head's offset from rest in the same space. The browser applies the VRM 0.x flip itself.
"""

import importlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import animate  # noqa: E402
import clips as clipdata  # noqa: E402
import collide  # noqa: E402
import fix  # noqa: E402
import pack  # noqa: E402
import rig  # noqa: E402

for m in (rig, animate, clipdata, collide, fix, pack):
    importlib.reload(m)

PACK = "annie-blender"
FPS = 30
UPPER = {"spine", "chest", "upperChest", "neck", "head"} | {b for b in rig.VRM if b.startswith(("left", "right")) and not any(
    k in b for k in ("Leg", "Foot", "Toes"))}
UPPER_LAYERS = ("talk", "gesture")


def bake(arm, n):
    """Sample frames 0..n-1: {vrm bone: [(x,y,z,w) per frame]}, hips offsets, and QA samples."""
    P = arm.pose.bones
    names = [b for b in rig.VRM if rig.VRM[b] in P]
    Rrest = {b: P[rig.VRM[b]].bone.matrix_local.to_3x3() for b in names}
    hips0 = P[rig.VRM["hips"]].bone.head_local.copy()
    out = {b: [] for b in names}
    hips = []
    qa = {"joints": []}
    C2B, B2C = rig.C2B, rig.B2C
    for f in range(n):
        bpy.context.scene.frame_set(f)
        D = {}
        for b in names:
            D[b] = B2C @ (P[rig.VRM[b]].matrix.to_3x3() @ Rrest[b].transposed()) @ C2B
        for b in names:
            p = rig.VRM_PARENT[b]
            L = D[b] if p is None else D[p].transposed() @ D[b]
            q = L.to_quaternion()
            prev = out[b][-1] if out[b] else None
            v = (q.x, q.y, q.z, q.w)
            if prev and sum(a * c for a, c in zip(prev, v)) < 0:
                v = tuple(-x for x in v)
            out[b].append(v)
        hips.append(tuple(B2C @ (P[rig.VRM["hips"]].head - hips0)))
        qa["joints"].append({b: tuple(rig.b2c(P[rig.VRM[b]].head)) for b in names})
        qa["joints"][-1].update({f"{b}_tail": tuple(rig.b2c(P[rig.VRM[b]].tail)) for b in ("leftHand", "rightHand", "head")})
    return out, hips, qa


def r4(x):
    return round(float(x), 4)


def clip_json(clip, bones, hips, n):
    loop = bool(clip.get("loop"))
    keep = UPPER if clip.get("layer") in UPPER_LAYERS else None
    d = {"fps": FPS, "duration": round(n / FPS if loop else (n - 1) / FPS, 4), "loop": loop, "frames": n,
         "space": "vrm1-normalized",
         "bones": {b: [r4(x) for q in qs for x in _unit(q)] for b, qs in bones.items() if keep is None or b in keep},
         "hips_position": [r4(x) for h in hips for x in (h if keep is None else (0, 0, 0))],
         "source": "hand-keyed on a Blender IK control rig (motion/blender/)", "license": "CC0-1.0"}
    if clip.get("energy"):
        d["energy"] = clip["energy"]
    return d


def _unit(q):
    s = math.sqrt(sum(x * x for x in q))
    return [x / s for x in q]


def check(clip, bones, hips, qa, n):
    """Numbers an animator would eyeball: foot slide, elbow bend sign, hands inside the torso, loop seam."""
    msgs = []
    J = qa["joints"]
    for s in ("left", "right"):
        a = [Vector(j[f"{s}Foot"]) for j in J]
        slide = max((p - a[0]).length for p in a)
        if slide > 0.004 and not clip.get("feet_move"):
            msgs.append(f"{s} ankle moves {slide * 100:.1f} cm")
        # hand vs torso: wrist distance to the spine axis (hips..neck) in x/z
        for i, j in enumerate(J):
            w = Vector(j[f"{s}Hand"])
            c = Vector(j["upperChest"])
            h = Vector(j["hips"])
            if h.y - 0.05 < w.y < c.y + 0.12:
                ax = Vector(j["chest"])
                dxz = math.hypot(w.x - ax.x, w.z - ax.z)
                if dxz < 0.07:  # wrist inside the torso volume (the cardigan adds ~2 cm)
                    msgs.append(f"{s} wrist {dxz * 100:.1f} cm from the spine axis at frame {i}")
                    break
    for s in ("left", "right"):  # stick-straight arms: the wrist target out of reach locks the elbow
        straight = sum(1 for q in bones[f"{s}LowerArm"] if 2 * math.degrees(math.acos(min(1.0, abs(q[3])))) < 10)
        if straight > 2:
            msgs.append(f"{s} elbow nearly straight (< 10 deg) on {straight} frames")
    if clip.get("loop"):
        seam = max(1 - abs(sum(a * b for a, b in zip(q[0], q[-1]))) for q in bones.values())
        step = max(max(1 - abs(sum(a * b for a, b in zip(q[i], q[i + 1]))) for i in range(len(q) - 1)) for q in bones.values())
        if seam > 3 * step + 1e-5:
            msgs.append(f"loop seam {seam:.2e} vs max step {step:.2e}")
    return msgs


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d  # noqa: E731
    out_dir = Path(opt("--out", ROOT / "assets/clips" / PACK))
    stats_dir = Path(opt("--stats", ROOT / "motion/raw/blender/stats"))
    only = set(opt("--only").split(",")) if opt("--only") else None
    fix_on = "--no-fix" not in argv
    out_dir.mkdir(parents=True, exist_ok=True)
    stats_dir.mkdir(parents=True, exist_ok=True)
    arm = rig.build(ROOT)
    keyer = animate.Keyer(arm)
    col = collide.Collider(arm)
    solver = fix.Solver(arm, col, FPS)
    qa_dump = {}
    for clip in clipdata.CLIPS:
        name = clip["name"]
        if only and name not in only:
            continue
        n, _ = animate.apply_clip(keyer, clip, FPS)
        if fix_on:
            history, m0, m1 = solver.solve(clip, n)
        else:
            m0 = m1 = solver.measure(n)
            history = []
        bones, hips, qa = bake(arm, n)
        for m in check(clip, bones, hips, qa, n):
            print(f"  QA {name}: {m}")
        d = clip_json(clip, bones, hips, n)
        s = json.dumps(d, separators=(",", ":"))
        (out_dir / f"{name}.json").write_text(s)
        st = {"name": name, "frames": n, "before": fix.summarize(m0), "after": fix.summarize(m1), "history": history}
        (stats_dir / f"{name}.json").write_text(json.dumps(st, indent=1))
        qa_dump[name] = qa["joints"]
        a, b = st["before"], st["after"]
        print(f"{name}: {n} frames, {len(s) / 1e3:.0f} kB; collide max {a['max_mm']} -> {b['max_mm']} mm, "
              f"frames > 5 mm {a['frames_gt5mm']} -> {b['frames_gt5mm']}", flush=True)
    if "--qa" in argv:
        (out_dir / "_qa_joints.json").write_text(json.dumps(qa_dump))
    if opt("--out") is None and "--no-pack" not in argv:
        pack.write(quiet=only is not None)  # keep pack.json in step with the clip files (the gate checks sha256)


if __name__ == "__main__":
    main()
