"""Velocity steps ("pops") in baked clip JSONs, the way the stage's body probe counts them (plain Python).

  python3 motion/blender/pops.py [PACK_DIR] [--all]

Per clip and body bone (demo/bodyprobe.mjs POP list: no fingers), the angular velocity between frames
(rotation vector of q[i-1]^-1 q[i], deg/frame) and its change per frame (deg/frame^2). A spike is an
acceleration above 4 deg/frame^2 that is more than 2.5x the mean of the 3 frames before and of the 3
after (motionStats in bodyprobe.mjs): a velocity step, not a fast authored stroke that ramps. Prints the
clips with spikes (--all: every clip) and the worst acceleration per clip.
"""

import json
import math
import sys
from pathlib import Path

POP = ("hips", "spine", "chest", "upperChest", "neck", "head", "leftShoulder", "rightShoulder", "leftUpperArm",
       "rightUpperArm", "leftLowerArm", "rightLowerArm", "leftHand", "rightHand", "leftUpperLeg", "rightUpperLeg",
       "leftLowerLeg", "rightLowerLeg", "leftFoot", "rightFoot")


def rotvec(a, b):
    """Rotation vector (rad) of a^-1 b for unit quaternions (x, y, z, w)."""
    ax, ay, az, aw = -a[0], -a[1], -a[2], a[3]
    bx, by, bz, bw = b
    x = aw * bx + ax * bw + ay * bz - az * by
    y = aw * by - ax * bz + ay * bw + az * bx
    z = aw * bz + ax * by - ay * bx + az * bw
    w = aw * bw - ax * bx - ay * by - az * bz
    if w < 0:
        x, y, z, w = -x, -y, -z, -w
    s = math.sqrt(x * x + y * y + z * z)
    if s < 1e-12:
        return (0.0, 0.0, 0.0)
    ang = 2 * math.atan2(s, w)
    return (x / s * ang, y / s * ang, z / s * ang)


def clip_stats(c):
    loop = c.get("loop")
    spikes, worst = [], (0.0, None, 0.0)
    for b in POP:
        arr = c["bones"].get(b)
        if not arr:
            continue
        q = [arr[i:i + 4] for i in range(0, len(arr), 4)]
        pad = 4 if loop else 0
        if loop:
            q = q[-pad:] + q + q[:pad]
        w = [rotvec(q[i - 1], q[i]) for i in range(1, len(q))]
        acc = [math.degrees(math.dist(w[i - 1], w[i])) for i in range(1, len(w))]
        for i, a in enumerate(acc):
            t = (i + 1 - pad) / c.get("fps", 30)
            if loop and not 0 <= t < c["duration"]:
                continue
            if a > worst[0]:
                worst = (a, b, t)
            if 3 <= i < len(acc) - 3 and a > 4:
                before, after = sum(acc[i - 3:i]) / 3, sum(acc[i + 1:i + 4]) / 3
                if a > 2.5 * max(before, after):
                    spikes.append((round(t, 2), b, round(a, 1)))
    return spikes, worst


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pack = Path(args[0]) if args else Path(__file__).resolve().parents[2] / "assets/clips/annie-blender"
    total = 0
    rows = []
    for f in sorted(pack.glob("*.json")):
        if f.name == "pack.json":
            continue
        spikes, worst = clip_stats(json.loads(f.read_text()))
        total += len(spikes)
        rows.append((f.stem, spikes, worst))
    for name, spikes, (a, b, t) in rows:
        if spikes or "--all" in sys.argv:
            s = "  ".join(f"{tt}s {bb} {aa}" for tt, bb, aa in sorted(spikes)[:6])
            print(f"{name:20s} worst {a:5.1f} deg/frame^2 ({b} at {t:.2f} s)  spikes {len(spikes)}  {s}")
    worst = max(rows, key=lambda r: r[2][0])
    print(f"{pack.name}: {len(rows)} clips, {total} spikes, worst acceleration {worst[2][0]:.1f} deg/frame^2 ({worst[0]})")


if __name__ == "__main__":
    main()
