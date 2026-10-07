"""Measure baked clip JSONs on the current avatar mesh by plain FK (compare packs, or a pack on a new outfit).

  blender -b -y --python motion/blender/fkcheck.py -- [--pack DIR] [--src DIR] [--clips a,b] [--out stats.json]

The control rig is built (for the collider's mesh classes and the outfit `hide`), then switched off:
constraints and drivers muted and every humanoid bone set straight from the JSON (the inverse of
export.bake). Gesture-layer clips carry only the upper body; the rest (hips, legs) comes from the
pack's idle_a frame 0, her standing pose. Contact windows and `hands_touch` come from the authoring
in --src (default motion/blender; e.g. an older checkout's motion/blender for an older pack), so the
numbers match what export.py reports for the same clip. Prints fix.summarize numbers per clip.
"""

import importlib.util
import json
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import collide  # noqa: E402
import fix  # noqa: E402
import rig  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def opt(k, d=None):
    return argv[argv.index(k) + 1] if k in argv else d


def authoring(src):
    """{clip: (contacts, hands_touch)} from clips.py in src (loaded without disturbing this checkout)."""
    saved = {k: sys.modules.pop(k) for k in ("poses", "phrases", "clips") if k in sys.modules}
    sys.path.insert(0, src)
    try:
        mods = {}
        for name in ("poses", "phrases", "clips"):
            spec = importlib.util.spec_from_file_location(name, f"{src}/{name}.py")
            mods[name] = importlib.util.module_from_spec(spec)
            sys.modules[name] = mods[name]
            spec.loader.exec_module(mods[name])
        return {c["name"]: (list(c.get("contacts", [])), bool(c.get("hands_touch"))) for c in mods["clips"].CLIPS}
    finally:
        sys.path.remove(src)
        for k in ("poses", "phrases", "clips"):
            sys.modules.pop(k, None)
        sys.modules.update(saved)


def main():
    pack = Path(opt("--pack", ROOT / "assets/clips/annie-blender"))
    only = opt("--clips")
    meta = authoring(str(opt("--src", HERE)))
    arm = rig.build(ROOT)
    col = collide.Collider(arm)
    P = arm.pose.bones
    for pb in P:
        for c in pb.constraints:
            c.mute = True
    if arm.animation_data:
        for fc in arm.animation_data.drivers:
            fc.mute = True
        arm.animation_data.action = None
    names = [b for b in rig.VRM if rig.VRM[b] in P]
    Rrest = {b: P[rig.VRM[b]].bone.matrix_local.to_3x3() for b in names}
    for b in names:
        P[rig.VRM[b]].rotation_mode = "QUATERNION"
    idle = json.loads((pack / "idle_a.json").read_text())
    fill = {b: idle["bones"][b][:4] for b in idle["bones"]}

    def set_frame(c, f):
        for b in names:
            arr = c["bones"].get(b)
            x, y, z, w = arr[4 * f:4 * f + 4] if arr else fill.get(b, (0, 0, 0, 1))
            L = rig.C2B @ Quaternion((w, x, y, z)).to_matrix() @ rig.B2C
            P[rig.VRM[b]].rotation_quaternion = (Rrest[b].transposed() @ L @ Rrest[b]).to_quaternion()
        hp = c.get("hips_position") or []
        hp = hp[3 * f:3 * f + 3] if any(hp) else idle["hips_position"][:3]
        P[rig.VRM["hips"]].location = Rrest["hips"].transposed() @ (rig.C2B @ Vector(hp))
        bpy.context.view_layer.update()

    res = {}
    for path in sorted(pack.glob("*.json")):
        name = path.stem
        if name == "pack" or (only and name not in only.split(",")):
            continue
        c = json.loads(path.read_text())
        contacts, touch = meta.get(name, ([], False))
        col.hand_vs_hand = not touch  # clasped hands: skip hand-vs-hand (as fix.Solver does)
        m = []
        for f in range(c["frames"]):
            set_frame(c, f)
            x = col.measure()
            x["contact"] = {}
            x["in_contact"] = {S: any(k[0] == S and k[1] <= f / c.get("fps", 30) <= k[2] for k in contacts) for S in ("L", "R")}
            m.append(x)
        s = fix.summarize(m)
        res[name] = {"frames": c["frames"], **s}
        print(f"{name:20s} max {s['max_mm']:6.1f} mm  >5mm {s['frames_gt5mm']:4d}  elbow {s['elbow_max_mm']:5.1f}  "
              f"gap hand {s['gap_hand_median_mm']}  elbow {s['gap_elbow_median_mm']}", flush=True)
    if opt("--out"):
        Path(opt("--out")).write_text(json.dumps(res, indent=1))


main()
