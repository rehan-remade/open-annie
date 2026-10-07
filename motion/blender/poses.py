"""Key-pose vocabulary for Annie's clips: the base stance, rest postures, and pose helpers.

Channels and units: animate.py. Wrist / pole positions are metres in the upper chest's frame at rest
(x = her left, y = up, z = forward; shoulder joints at y 1.25, hips 0.90). She wears the outfit the
stage shows (AvatarSample_B without the varsity jacket, avatars/index.json `hide`): a crop top, a bare
midriff and a pleated skirt, so resting arms lie on the skirt sides and the elbows lightly on the
waist, skin is firm; collide.py / fix.py measure every frame on the deformed mesh and push anything
that still sinks in back out.
"""

import copy
import os
from pathlib import Path

# The hero avatar (pixiv VRoid AvatarSample_B). ANNIE_AVATAR=<repo-relative path> overrides.
# Same VRoid skeleton as AvatarSample_A.
AVATAR_CANDIDATES = ["assets/avatars/annie-b/model.vrm"]


def avatar_hide(root, avatar):
    """The outfit parts the runtime removes at load, from assets/avatars/index.json (`hide`), so the
    pack is animated and collision-checked against exactly what is shown. ANNIE_OUTFIT=full keeps all."""
    import json
    if os.environ.get("ANNIE_OUTFIT") == "full":
        return {}
    try:
        idx = json.loads((Path(root) / "assets/avatars/index.json").read_text())
    except (OSError, ValueError):
        return {}
    for e in idx:
        if e.get("path") == avatar:
            return e.get("hide") or {}
    return {}


def avatar_path(root):
    if os.environ.get("ANNIE_AVATAR"):
        return os.environ["ANNIE_AVATAR"]
    for c in AVATAR_CANDIDATES:
        if (Path(root) / c).exists():
            return c
    raise FileNotFoundError(AVATAR_CANDIDATES)

# Relaxed contrapposto: weight on the left leg (hip hiked, knee nearly straight), right foot forward
# and turned out with a soft knee; pelvis yawed toward the free side, shoulders counter-rotated and
# counter-tilted; soft elbows, dropped shoulders, loose fingers.
BASE = {
    "cog": (0.026, -0.022, 0.012), "cog_rot": (1.0, 5.0, 4.5), "torso": (1.5, 5.5, -6.0), "head": (-1.5, -2.0, -3.5),
    "aim": 0.0,
    "hand_L": (0.176, 0.841, 0.08), "hand_R": (-0.17, 0.847, 0.079),
    "elbow_L": (0.072, 0.952, -0.303), "elbow_R": (-0.06, 0.95, -0.3),
    "wrist_L": (10, -4, -16), "wrist_R": (12, -5, -12),
    "fingers_L": (0.42, 0.12, 0.35, 0.45), "fingers_R": (0.45, 0.14, 0.32, 0.45),
    "fcurl_L": (-0.08, 0.0, 0.06, 0.14), "fcurl_R": (-0.05, 0.02, 0.08, 0.16),
    "shoulder_L": (-2.5, 1.0), "shoulder_R": (-2.0, 1.5),
    "foot_L": (0.012, 0, 0.0, 0, 5, 0), "foot_R": (-0.022, 0, 0.055, 0, -16, 0), "toes_L": 0, "toes_R": 0,
}

LEFT_RIGHT = [("hand", "pos"), ("elbow", "pos"), ("wrist", "same"), ("fingers", "same"), ("fcurl", "same"),
              ("shoulder", "same"), ("foot", "foot"), ("toes", "same")]


class Seq:
    """Key poses over time. Each key carries every channel forward from the previous key.

    s.k(t, *dicts, ease=None, **channels)  set channels (dicts merged first, then keywords)
    s.d(t, ease=None, **deltas)            offset channels relative to the current key
    s.to(t, pose, *dicts, ease=None, **ch) jump to a whole pose (e.g. back to BASE)
    """

    def __init__(self, start=None):
        self.cur = copy.deepcopy(start or BASE)
        self.keys = []

    def _key(self, t, ease):
        self.keys.append((round(t, 4), copy.deepcopy(self.cur), {"ease": ease} if ease else {}))
        return self

    def k(self, t, *parts, ease=None, **kw):
        for p in parts:
            self.cur.update(p)
        self.cur.update(kw)
        return self._key(t, ease)

    def d(self, t, ease=None, **delta):
        self.cur = add(self.cur, **delta)
        return self._key(t, ease)

    def to(self, t, pose, *parts, ease=None, **kw):
        self.cur = copy.deepcopy(pose)
        return self.k(t, *parts, ease=ease, **kw)


def P(base=None, **kw):
    out = copy.deepcopy(base or BASE)
    out.update(kw)
    return out


def mirror(p):
    """Mirror a pose (or a partial override) left <-> right across her midline."""
    out = {}
    for ch, v in p.items():
        if ch in ("cog", "look"):
            out[ch] = (-v[0], v[1], v[2])
        elif ch in ("cog_rot", "head"):
            out[ch] = (v[0], -v[1], -v[2])
        elif ch == "torso":
            out[ch] = (v[0], -v[1], -v[2])
        elif ch[-2:] in ("_L", "_R"):
            base, side = ch[:-2], ch[-1]
            o = f"{base}_{'R' if side == 'L' else 'L'}"
            kind = dict(LEFT_RIGHT).get(base, "same")
            if kind == "pos":
                out[o] = (-v[0], v[1], v[2])
            elif kind == "foot":
                x, y, z, pitch, yaw, roll = (list(v) + [0, 0, 0])[:6]
                out[o] = (-x, y, z, pitch, -yaw, -roll)
            else:
                out[o] = v
        else:
            out[ch] = v
    return out


def add(p, **delta):
    """Offset channels of a pose by tuples (element-wise)."""
    out = dict(p)
    for ch, d in delta.items():
        v = out[ch]
        out[ch] = tuple(a + b for a, b in zip(v, d)) if isinstance(v, tuple) else v + d
    return out


def heel(side, s, base=None):
    """Heel lift (the ball of the foot stays down) by s in 0..1: ~20 deg foot pitch + toe bend."""
    b = (base or BASE)[f"foot_{side}"]
    return {f"foot_{side}": (b[0], b[1] + 0.034 * s, b[2] - 0.006 * s, b[3] + 20 * s, b[4], b[5]), f"toes_{side}": 20 * s}


def fists(side, s=1.0):
    return {f"fingers_{side}": (0.4 + 0.55 * s, 0.1, 0.3 + 0.65 * s, 0.45 - 0.1 * s), f"fcurl_{side}": (0, 0, 0, 0)}


def point(side, s=1.0):
    """Index extended, others curled (pointing / counting one)."""
    return {f"fingers_{side}": (0.35 + 0.55 * s, 0.05, 0.35 + 0.5 * s, 0.5), f"fcurl_{side}": (-0.95 * s, 0.05, 0.05, 0.1)}


OPEN = (0.1, 0.65, 0.1, 0.1)  # relaxed-open, spread (wave, surprise, big open)
SOFT = (0.3, 0.28, 0.25, 0.35)  # talking hand: loose, alive
PALM = (0.16, 0.4, 0.15, 0.2)  # open palm offer
ZERO4 = (0, 0, 0, 0)
LOOSE_FC = (-0.06, 0.0, 0.07, 0.15)

# ---------------------------------------------------------------- rest postures
# Talk rests (upper body; the base clip keeps the legs). low: arms relaxed, hands just in front of
# the hips, a little forward of the base pose (in front of the belly they need the elbows out, which
# reads as hands-on-hips). mid: hands at waist height, forearms about level, ready to
# gesture, the elbows lightly on the waist (the poles sit medially: the torso leans to her left, so
# the right one is further in). Phrases start and end exactly on one of these.
REST_LOW = P(hand_L=(0.144, 0.87, 0.087), hand_R=(-0.132, 0.872, 0.08), elbow_L=(0.162, 0.966, -0.302), elbow_R=(-0.06, 0.95, -0.3),
             wrist_L=(8, -3, 5), wrist_R=(8, -3, 8), fingers_L=(0.36, 0.2, 0.3, 0.4), fingers_R=(0.38, 0.2, 0.3, 0.4),
             fcurl_L=LOOSE_FC, fcurl_R=(-0.04, 0.02, 0.09, 0.17))
REST_MID = P(hand_L=(0.12, 0.99, 0.2), hand_R=(-0.12, 0.985, 0.2), elbow_L=(0.12, 0.9, -0.3), elbow_R=(-0.057, 0.9, -0.3),
             wrist_L=(0, 0, 35), wrist_R=(0, 0, 38), fingers_L=SOFT, fingers_R=(0.32, 0.3, 0.25, 0.35),
             fcurl_L=LOOSE_FC, fcurl_R=(-0.04, 0.02, 0.09, 0.17))
RESTS = {"low": REST_LOW, "mid": REST_MID}

# Hands loosely clasped low in front (idle_c, listen_b, bow): left over right, forearms resting on the
# front of the skirt, not through it.
CLASP = dict(hand_L=(0.027, 0.881, 0.169), hand_R=(-0.046, 0.87, 0.158), elbow_L=(0.246, 0.984, -0.092), elbow_R=(-0.228, 0.962, -0.157),
             wrist_L=(15, -5, -45), wrist_R=(20, 5, -20), fingers_L=(0.3, 0.1, 0.3, 0.5), fingers_R=(0.45, 0.1, 0.3, 0.5),
             fcurl_L=(0, 0.02, 0.06, 0.12), fcurl_R=(0, 0.02, 0.06, 0.12))
# Right hand holding the left forearm just above the wrist (a shy listening pose).
HOLD = dict(hand_L=(0.087, 0.872, 0.142), elbow_L=(0.229, 0.964, -0.168), wrist_L=(10, 0, -20), fingers_L=(0.4, 0.1, 0.35, 0.45),
            hand_R=(-0.005, 0.904, 0.158), elbow_R=(-0.227, 0.986, -0.066), wrist_R=(10, 5, -40), fingers_R=(0.35, 0.05, 0.4, 0.5),
            fcurl_R=(0, 0.03, 0.06, 0.1))

POSES = {
    "BASE": BASE, "REST_LOW": REST_LOW, "REST_MID": REST_MID, "CLASP": P(**CLASP), "HOLD": P(**HOLD),
    "BASE_mirror": mirror(BASE),
}
