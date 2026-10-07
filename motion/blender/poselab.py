"""Pose lab: collision numbers for static key poses, and the correction the solver would add.

  blender -b -y --python motion/blender/poselab.py -- BASE,REST_LOW [--dots DIR]

For each named pose (poses.POSES, or any `NAME` exported by clips.py / phrases.py as a dict), key it
as a 1-frame clip, measure it on the deformed mesh, run the collision solver, and print per arm the
hand / forearm / elbow depth before -> after and the wrist / pole offsets (in the control's own
axes: x = her left, y = up, z = forward, metres) to fold back into the authored pose.
"""

import importlib
import json
import sys
from pathlib import Path

import bpy
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import animate  # noqa: E402
import collide  # noqa: E402
import fix  # noqa: E402
import poses  # noqa: E402
import rig  # noqa: E402

for m in (rig, animate, collide, fix, poses):
    importlib.reload(m)


def lookup(name):
    if name in poses.POSES:
        return poses.POSES[name]
    for modname in ("clips", "phrases"):
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            continue
        if hasattr(mod, "POSES") and name in mod.POSES:
            return mod.POSES[name]
        if hasattr(mod, name):
            return getattr(mod, name)
    raise KeyError(name)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    names = argv[0].split(",")
    dots = Path(argv[argv.index("--dots") + 1]) if "--dots" in argv else None
    bake_dir = Path(argv[argv.index("--bake") + 1]) if "--bake" in argv else None
    arm = rig.build(ROOT)
    keyer = animate.Keyer(arm)
    col = collide.Collider(arm)
    solver = fix.Solver(arm, col)
    for name in names:
        pose = poses.P(**lookup(name)) if "cog" not in lookup(name) else lookup(name)
        clip = {"name": name, "loop": True, "duration": 2 / 30, "keys": [(0, pose), (2 / 30, pose)]}
        if "--attach" in argv:  # e.g. --attach L:torso:0.003,R:torso:0.003 -> pull those hands onto the surface
            clip["contacts"] = [(c.split(":")[0], -1, 1, c.split(":")[1], "hand", float(c.split(":")[2]))
                                for c in argv[argv.index("--attach") + 1].split(",")]
        if "--touch" in argv:
            clip["hands_touch"] = True
        n, _ = animate.apply_clip(keyer, clip, 30)
        col.debug = dots is not None
        col.dbg = []
        if "--raw" in argv:
            m0 = m1 = solver.measure(n)
            hist = []
        else:
            hist, m0, m1 = solver.solve(clip, n, log=lambda *_: None)
        if dots:
            dots.mkdir(parents=True, exist_ok=True)
            (dots / f"{name}.json").write_text(json.dumps([[*rig.b2c(p[:3]), p[3], p[4]] for p in col.dbg]))
        if bake_dir:
            import export
            bake_dir.mkdir(parents=True, exist_ok=True)
            bones, hips, _ = export.bake(arm, n)
            (bake_dir / f"{name}.json").write_text(json.dumps(export.clip_json(clip, bones, hips, n), separators=(",", ":")))
        bpy.context.scene.frame_set(0)
        P = arm.pose.bones
        print(f"{name}: hand/forearm/elbow/contact mm per iteration: {hist}")
        for S in ("L", "R"):
            a, b = m0[0][S], m1[0][S]
            fh = np.array(P[f"FIX_hand_{S}"].location)
            fe = np.array(P[f"FIX_elbow_{S}"].location)
            wrist = rig.b2c(P[rig.VRM[f"{'left' if S == 'L' else 'right'}Hand"]].head)
            gt, gh = col.contact_gap(S, "torso")[0], col.contact_gap(S, "head")[0]
            print(f"  {S}: gap to torso {gt * 1000:6.1f} mm, to head {gh * 1000:6.1f} mm | arm clearance: hand/forearm "
                  f"{b['gap_hand'] * 1000:6.1f} mm, elbow {b['gap_elbow'] * 1000:6.1f} mm")
            print(f"  {S}: hand {a['hand'] * 1000:5.1f}->{b['hand'] * 1000:4.1f}  forearm {a['forearm'] * 1000:5.1f}->{b['forearm'] * 1000:4.1f}"
                  f"  elbow {a['elbow'] * 1000:5.1f}->{b['elbow'] * 1000:4.1f} mm | add hand_{S} {np.round(fh, 3).tolist()}"
                  f" elbow_{S} {np.round(fe, 3).tolist()} | wrist now {np.round(np.array(wrist), 3).tolist()}")


if __name__ == "__main__":
    main()
