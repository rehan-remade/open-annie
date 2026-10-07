"""Write assets/clips/annie-blender/pack.json from the baked clip files + the clip metadata in clips.py.

  python3 motion/blender/pack.py            # after export.py / build.sh; plain Python, no Blender

Also prints the collision table (motion/raw/blender/stats/<clip>.json, written by export.py):
before = the authored keys, after = with the solver's corrections (docs: motion/README.md).
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import clips as clipdata  # noqa: E402
import poses  # noqa: E402

PACK = "annie-blender"
FINGERS = ("Thumb", "Index", "Middle", "Ring", "Little")
ENTRY_KEYS = ("kind", "when", "layer", "energy", "hands", "rest", "prep_end", "stroke", "strokes", "retract_start")


def write(out_dir=None, stats_dir=None, quiet=False):
    out_dir = Path(out_dir or ROOT / "assets/clips" / PACK)
    stats_dir = Path(stats_dir or ROOT / "motion/raw/blender/stats")
    names = [c["name"] for c in clipdata.CLIPS]
    for old in out_dir.glob("*.json"):
        if old.name != "pack.json" and old.stem not in names:
            old.unlink()
    entries = {}
    hands = None
    avatar = poses.avatar_path(ROOT)
    for c in clipdata.CLIPS:
        f = out_dir / f"{c['name']}.json"
        b = f.read_bytes()
        d = json.loads(b)
        e = {"file": f.name, "loop": d["loop"], "duration": d["duration"], "frames": d["frames"],
             "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
        for k in ENTRY_KEYS:
            if c.get(k) is not None:
                e[k] = c[k]
        # The runtime plays contact phrases at their authored size (energy scaling would move the
        # hand off the chest or into it; docs/contracts.md "Gesture phrases").
        if c.get("layer") == "gesture" and (c.get("contacts") or c.get("hands_touch")):
            e["contact"] = True
        entries[c["name"]] = e
        if c["name"] == "idle_a":
            hands = {k: v[:4] for k, v in d["bones"].items() if any(x in k for x in FINGERS)}
    pack = {
        "id": PACK, "format": "open-annie motion pack v1 (docs/contracts.md)", "space": "vrm1-normalized", "fps": 30,
        "generator": "hand-keyed in Blender 5.2 on an IK control rig over Annie's VRM armature, collision-fixed on the "
                     "deformed mesh (motion/blender/)",
        "sources": {"rig_and_keys": "motion/blender/ (rig.py, clips.py, animate.py, collide.py, fix.py, export.py), authored in this repo",
                    "vrm_importer": "https://github.com/saturday06/VRM-Addon-for-Blender (MIT; used as a tool only)"},
        "license": "CC0-1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "authors": ["open-annie authors (keyframe animation)"],
        "description": clipdata.DESCRIPTION,
        "avatar": {"id": "annie-b" if "annie-b" in avatar else "AvatarSample_B", "file": avatar,
                   "sha256": hashlib.sha256((ROOT / avatar).read_bytes()).hexdigest()},
        "hands": hands,
        "hands_note": "the base pose's relaxed fingers, for any clip that leaves the fingers undriven",
        "rests": clipdata.RESTS_NOTE,
        "clips": entries,
    }
    (out_dir / "pack.json").write_text(json.dumps(pack, indent=1, ensure_ascii=False) + "\n")
    if not quiet:
        print(f"{PACK}: {len(entries)} clips")
        table(stats_dir, names)
    return pack


def table(stats_dir, names):
    rows = []
    for n in names:
        p = Path(stats_dir) / f"{n}.json"
        if not p.exists():
            continue
        s = json.loads(p.read_text())
        a, b = s["before"], s["after"]
        g = lambda v: "  -  " if v is None else f"{v:5.1f}"  # noqa: E731
        rows.append((n, s["frames"], a["max_mm"], a["mean_mm"], a["frames_gt5mm"], b["max_mm"], b["mean_mm"], b["frames_gt5mm"],
                     a["elbow_max_mm"], b["elbow_max_mm"], b["elbow_frames_over_allowance"], g(b.get("gap_hand_median_mm")),
                     g(b.get("gap_elbow_median_mm"))))
    print("hands, fingers, forearms: hard, target 0 frames > 5 mm | elbow end vs the waist: 5 mm (skin) | gaps: median clearance to torso / skirt / thighs")
    print(f"{'clip':24s} {'frames':>6s} | {'before max':>10s} {'mean':>6s} {'>5mm':>5s} | {'after max':>9s} {'mean':>6s} {'>5mm':>5s} "
          f"| {'elbow before':>12s} {'after':>6s} {'>allow':>6s} | {'gap hand':>8s} {'elbow':>6s}")
    for r in rows:
        print(f"{r[0]:24s} {r[1]:6d} | {r[2]:10.1f} {r[3]:6.1f} {r[4]:5d} | {r[5]:9.1f} {r[6]:6.1f} {r[7]:5d} | {r[8]:12.1f} {r[9]:6.1f} {r[10]:6d}"
              f" | {r[11]:>8s} {r[12]:>6s}")


if __name__ == "__main__":
    write()
