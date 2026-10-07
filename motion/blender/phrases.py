"""Co-speech gesture phrases (layer "gesture", docs/contracts.md "Gesture phrases").

Short upper-body phrases the runtime scheduler plays while Annie speaks, aligned so the stroke lands
on speech emphasis. Each phrase starts and ends exactly on its talk rest (poses.REST_LOW / REST_MID)
and carries three markers (seconds into the clip): prep_end (the hand has arrived where the stroke
starts), stroke (peak emphasis: the beat), retract_start (the hold ends, the hand heads home).

A phrase is a list of keys (t, overrides) on top of its rest: overrides are absolute channels, or
`d_<channel>` deltas added to the rest's value (head, torso, shoulders). Left-hand variants are
mirrored from the right-hand ones.
"""

from poses import OPEN, PALM, REST_LOW, REST_MID, SOFT, ZERO4, Seq, mirror, point

RESTS = {"low": REST_LOW, "mid": REST_MID}
PHRASES = []
POSES = {}
LAG = {"head": 3, "fingers": 3, "wrist": 2}


def _apply(base, ov):
    out = {}
    for k, v in ov.items():
        if k.startswith("d_"):
            ch = k[2:]
            b = base[ch]
            out[ch] = tuple(x + y for x, y in zip(b, v))
        else:
            out[k] = v
    return out


def _mirror_ov(ov):
    plain = {k: v for k, v in ov.items() if not k.startswith("d_")}
    deltas = {k[2:]: v for k, v in ov.items() if k.startswith("d_")}
    out = mirror(plain)
    for ch, v in mirror(deltas).items():
        out["d_" + ch] = v
    return out


HOVER = 0.06  # s: the pre-stroke pose is held this long in the clip itself


def hold_points(prep_end, stroke, strokes=None):
    """Where the scheduler parks before each stroke (gestures.js): prep_end, then for a multi-stroke
    phrase each later beat's stroke minus the first approach (stroke - prep_end), after the previous beat."""
    S = sorted(strokes) if strokes else [stroke]
    L = S[0] - prep_end
    return [prep_end] + [round(max(S[k - 1] + 0.06, S[k] - L), 3) for k in range(1, len(S))]


def _through(base, keys, i, fps=30):
    """Is a hand still moving at key i? Speed through the key as the auto-clamped Bezier sees it: per
    channel, zero where the key is an extremum, else (next - prev) / (t_next - t_prev); m/s."""
    import math
    pose = lambda j: {**base, **_apply(base, keys[j][1])} if j >= 0 else base  # noqa: E731
    t0 = keys[i - 1][0] if i > 0 else 0.0
    t2 = keys[i + 1][0] if i + 1 < len(keys) else t0
    a, b, c = pose(i - 1), pose(i), pose(i + 1) if i + 1 < len(keys) else base
    for S in ("L", "R"):
        v = [0.0 if (y - x) * (z - y) <= 0 else (z - x) / max(t2 - t0, 1e-3)
             for x, y, z in zip(a[f"hand_{S}"], b[f"hand_{S}"], c[f"hand_{S}"])]
        if math.hypot(*v) > 0.12:
            return True
    return False


def _hovered(base, keys, holds):
    """Make a hold point a real hold where the hand would otherwise move through it: the key there is
    repeated HOVER earlier (flat Bezier handles), so the hand is still where the scheduler parks it and
    the time-warped stroke fires from rest. (A hold in the middle of a moving segment is a velocity step
    twice: when the playback rate drops to the creep and when it jumps to the strike.) A beat's lift,
    which turns at the hold anyway, is left alone."""
    keys = list(keys)
    for h in holds:
        i = min(range(len(keys)), key=lambda j: abs(keys[j][0] - h))
        t = keys[i][0]
        prev = keys[i - 1][0] if i > 0 else 0.0
        if abs(t - h) > 0.035 or t - HOVER - prev < 0.05 or not _through(base, keys, i):
            continue
        keys.insert(i, (round(t - HOVER, 3), keys[i][1]))
    return keys


def phrase(name, hands, kind, energy, rest, when, dur, prep_end, stroke, retract_start, keys, lag=LAG, contacts=(), hands_touch=False,
           strokes=None):
    base = RESTS[rest]
    s = Seq(base)
    s.k(0)
    keys = _hovered(base, keys, hold_points(prep_end, stroke, strokes))
    for key in keys:
        t, ov = key[0], key[1]
        ease = key[2] if len(key) > 2 else None
        s.to(t, base, _apply(base, ov), ease=ease)
    s.to(dur, base)
    PHRASES.append({"name": name, "duration": dur, "loop": False, "keys": s.keys, "layer": "gesture", "kind": kind,
                    "hands": hands, "energy": energy, "rest": rest, "when": when, "prep_end": prep_end, "stroke": stroke,
                    "retract_start": retract_start, "lag": lag, "contacts": list(contacts), "hands_touch": hands_touch,
                    **({"strokes": list(strokes)} if strokes else {})})
    for key in keys:
        if abs(key[0] - stroke) < 1e-6:
            POSES[name + "@stroke"] = {**base, **_apply(base, key[1])}


def mirrored(name, hands, energy, keys, contacts=(), **kw):
    """Left-hand twin of a right-hand phrase's keys."""
    mk = [(k[0], _mirror_ov(k[1]), *k[2:]) for k in keys]
    mc = [(("L" if c[0] == "R" else "R"), *c[1:]) for c in contacts]
    return dict(name=name, hands=hands, energy=energy, keys=mk, contacts=mc, **kw)


# ------------------------------------------------------------------------------ offers / open palms
OFFER_R = [
    (0.2, dict(hand_R=(-0.131, 0.863, 0.075), wrist_R=(12, -3, 0))),
    (0.45, dict(hand_R=(-0.17, 0.93, 0.25), wrist_R=(0, 0, 45), fingers_R=(0.3, 0.3, 0.25, 0.3))),
    (0.7, dict(hand_R=(-0.19, 0.975, 0.31), wrist_R=(-8, 0, 78), fingers_R=PALM, fcurl_R=(0, 0, 0.05, 0.1), d_head=(2, -3, -4))),
    (0.95, dict(hand_R=(-0.19, 0.967, 0.305), wrist_R=(-6, 0, 75), fingers_R=PALM, fcurl_R=(0, 0, 0.05, 0.1), d_head=(1, -3, -4))),
    (1.25, dict(hand_R=(-0.188, 0.962, 0.3), wrist_R=(-4, 0, 72), fingers_R=(0.2, 0.35, 0.18, 0.22), d_head=(0.5, -2, -3))),
    (1.6, dict(hand_R=(-0.2, 0.9, 0.2), wrist_R=(5, 0, 25), fingers_R=(0.32, 0.25, 0.28, 0.35))),
]
phrase("g_offer_r", "right", "metaphoric", "calm", "low", "offering an idea, 'here's the thing'", 1.9, 0.45, 0.7, 1.25, OFFER_R)
phrase(**mirrored("g_offer_l", "left", "calm", OFFER_R), kind="metaphoric", rest="low", when="offering an idea, 'here's the thing'",
       dur=1.9, prep_end=0.45, stroke=0.7, retract_start=1.25)
phrase("g_offer_both", "both", "metaphoric", "calm", "low", "presenting something, 'so, this is it'", 2.0, 0.5, 0.75, 1.3, [
    (0.22, dict(hand_R=(-0.131, 0.863, 0.075), hand_L=(0.13, 0.859, 0.065), wrist_R=(12, -3, 0), wrist_L=(12, -3, 0))),
    (0.5, dict(hand_R=(-0.17, 0.93, 0.25), hand_L=(0.172, 0.935, 0.25), wrist_R=(0, 0, 45), wrist_L=(0, 0, 42))),
    (0.75, dict(hand_R=(-0.2, 0.97, 0.3), hand_L=(0.205, 0.975, 0.3), wrist_R=(-8, 0, 76), wrist_L=(-8, 0, 72), fingers_R=PALM,
                fingers_L=PALM, fcurl_R=(0, 0, 0.05, 0.1), fcurl_L=(0, 0, 0.05, 0.1), d_head=(3, 0, -2), d_torso=(-1, 0, 0))),
    (1.3, dict(hand_R=(-0.198, 0.962, 0.295), hand_L=(0.2, 0.968, 0.295), wrist_R=(-5, 0, 72), wrist_L=(-5, 0, 70), fingers_R=PALM,
               fingers_L=PALM, d_head=(1, 0, -2))),
    (1.68, dict(hand_R=(-0.2, 0.9, 0.2), hand_L=(0.2, 0.9, 0.2), wrist_R=(5, 0, 25), wrist_L=(5, 0, 22))),
])
phrase("g_open_both", "both", "metaphoric", "animated", "mid", "opening up, 'it could be anything', 'so many options'", 1.6, 0.35, 0.55, 1.0, [
    (0.18, dict(hand_R=(-0.1, 0.98, 0.24), hand_L=(0.095, 0.985, 0.24), wrist_R=(10, 0, 20), wrist_L=(10, 0, 18))),
    (0.35, dict(hand_R=(-0.2, 1.02, 0.27), hand_L=(0.2, 1.025, 0.27), wrist_R=(-5, 0, 60), wrist_L=(-5, 0, 58))),
    (0.55, dict(hand_R=(-0.27, 1.03, 0.26), hand_L=(0.275, 1.035, 0.26), wrist_R=(-12, 0, 82), wrist_L=(-12, 0, 80), fingers_R=OPEN,
                fingers_L=OPEN, fcurl_R=ZERO4, fcurl_L=ZERO4, d_shoulder_L=(3, 0), d_shoulder_R=(3, 0), d_head=(-2, 0, 3), d_torso=(-2, 0, 0))),
    (0.78, dict(hand_R=(-0.268, 1.025, 0.258), hand_L=(0.272, 1.03, 0.258), wrist_R=(-10, 0, 80), wrist_L=(-10, 0, 78), fingers_R=OPEN,
                fingers_L=OPEN, d_shoulder_L=(2, 0), d_shoulder_R=(2, 0), d_head=(-1, 0, 3), d_torso=(-1.5, 0, 0))),
    (1.0, dict(hand_R=(-0.26, 1.02, 0.255), hand_L=(0.265, 1.025, 0.255), wrist_R=(-8, 0, 76), wrist_L=(-8, 0, 74),
               fingers_R=(0.18, 0.5, 0.15, 0.2), fingers_L=(0.18, 0.5, 0.15, 0.2), d_head=(0, 0, 2))),
    (1.35, dict(hand_R=(-0.142, 0.991, 0.203), hand_L=(0.141, 0.994, 0.201), wrist_R=(0, 0, 50), wrist_L=(0, 0, 48))),
])

# ------------------------------------------------------------------------------ beats
BEAT_R = [
    (0.3, dict(hand_R=(-0.112, 1.026, 0.208), wrist_R=(-15, 0, 30), fingers_R=(0.25, 0.3, 0.22, 0.3))),
    (0.42, dict(hand_R=(-0.117, 0.963, 0.213), wrist_R=(12, 0, 28), fingers_R=(0.3, 0.25, 0.25, 0.32), d_head=(2.5, 0, 0))),
    (0.55, dict(hand_R=(-0.115, 0.979, 0.21), wrist_R=(6, 0, 30), d_head=(1, 0, 0))),
    (0.72, dict(hand_R=(-0.114, 0.981, 0.206), wrist_R=(3, 0, 34))),
]
phrase("g_beat_r", "right", "beat", "calm", "mid", "a small emphasis on a word", 1.2, 0.3, 0.42, 0.72, BEAT_R)
phrase(**mirrored("g_beat_l", "left", "animated", BEAT_R), kind="beat", rest="mid", when="a small emphasis on a word",
       dur=1.15, prep_end=0.28, stroke=0.4, retract_start=0.7)
phrase("g_beat_both", "both", "beat", "excited", "mid", "a strong emphasis, 'really', 'so much'", 1.2, 0.28, 0.4, 0.7, [
    (0.28, dict(hand_R=(-0.15, 1.065, 0.25), hand_L=(0.15, 1.07, 0.25), wrist_R=(-20, 0, 30), wrist_L=(-20, 0, 28),
                fingers_R=(0.2, 0.4, 0.2, 0.25), fingers_L=(0.2, 0.4, 0.2, 0.25), d_shoulder_L=(4, 0), d_shoulder_R=(4, 0), d_head=(-3, 0, 0))),
    (0.4, dict(hand_R=(-0.127, 0.966, 0.223), hand_L=(0.126, 0.969, 0.221), wrist_R=(15, 0, 28), wrist_L=(15, 0, 26),
               fingers_R=(0.3, 0.3, 0.25, 0.3), fingers_L=(0.3, 0.3, 0.25, 0.3), d_head=(4, 0, 0), d_torso=(2, 0, 0))),
    (0.52, dict(hand_R=(-0.124, 0.983, 0.218), hand_L=(0.123, 0.986, 0.216), wrist_R=(8, 0, 30), wrist_L=(8, 0, 28), d_head=(1.5, 0, 0),
                d_torso=(1, 0, 0))),
    (0.7, dict(hand_R=(-0.12, 0.986, 0.208), hand_L=(0.119, 0.989, 0.206), wrist_R=(4, 0, 34), wrist_L=(4, 0, 32))),
])
phrase("g_beat_excited_r", "right", "beat", "excited", "mid", "an excited emphasis, 'and then!'", 1.25, 0.3, 0.42, 0.75, [
    (0.3, dict(hand_R=(-0.15, 1.1, 0.26), wrist_R=(-22, 0, 25), fingers_R=OPEN, fcurl_R=ZERO4, d_shoulder_R=(5, 0), d_head=(-4, -2, -2))),
    (0.42, dict(hand_R=(-0.127, 0.966, 0.238), wrist_R=(15, 0, 20), **point("R", 0.6), d_head=(5, -2, -2), d_torso=(3, 0, -2))),
    (0.56, dict(hand_R=(-0.124, 0.986, 0.233), wrist_R=(6, 0, 24), **point("R", 0.5), d_head=(1, -1, -1), d_torso=(1, 0, -1))),
    (0.75, dict(hand_R=(-0.12, 0.991, 0.218), wrist_R=(3, 0, 30), fingers_R=SOFT)),
])

# ------------------------------------------------------------------------------ flicks, rolls
phrase("g_flick_r", "right", "metaphoric", "animated", "mid", "brushing a point aside, 'anyway', 'whatever'", 1.4, 0.3, 0.48, 0.85, [
    (0.3, dict(hand_R=(-0.092, 1.011, 0.213), wrist_R=(25, 0, 30), fingers_R=(0.65, 0.05, 0.6, 0.6), fcurl_R=(0, 0, 0.05, 0.1))),
    (0.48, dict(hand_R=(-0.235, 1.03, 0.26), wrist_R=(-25, -10, 58), fingers_R=(0.05, 0.6, 0.05, 0.1), fcurl_R=ZERO4, d_head=(-2, -4, -4))),
    (0.62, dict(hand_R=(-0.24, 1.025, 0.258), wrist_R=(-18, -6, 60), fingers_R=(0.12, 0.5, 0.1, 0.15), d_head=(-1, -5, -5))),
    (0.85, dict(hand_R=(-0.235, 1.02, 0.255), wrist_R=(-12, -4, 58), fingers_R=(0.2, 0.4, 0.18, 0.22), d_head=(0, -4, -4))),
    (1.15, dict(hand_R=(-0.142, 0.991, 0.203), wrist_R=(0, 0, 45))),
])


def roll(side_sign, cx, cy, cz, r, t0, turns, dt):
    """Forward-rolling circle keys (top -> front -> bottom -> back) for one hand, sagittal plane."""
    keys = []
    pts = [(0, r), (r, 0), (0, -r), (-r, 0)]  # (dz, dy)
    t = t0
    for i in range(int(turns * 4)):
        dz, dy = pts[i % 4]
        keys.append((round(t, 3), (cx * side_sign, cy + dy, cz + dz), 10 * dy / r, 45 + 12 * dz / r))
        t += dt
    return keys


def roll_phrase(name, hands, energy, dur):
    keys = []
    rk = roll(-1, 0.15, 1.035, 0.27, 0.035, 0.3, 2, 0.14)
    for i, (t, p, flex, tw) in enumerate(rk):
        ov = {"hand_R": p, "wrist_R": (flex, 0, tw), "fingers_R": SOFT}
        if hands == "both":
            q = (-p[0] + 0.005, p[1] + 0.004, p[2] - 0.004)
            ov.update({"hand_L": q, "wrist_L": (flex, 0, tw - 3), "fingers_L": SOFT})
        if i == 2:
            ov["d_head"] = (2, -2 if hands == "right" else 0, -2 if hands == "right" else 0)
        keys.append((t, ov))
    last = rk[-1][0]
    keys.append((last + 0.25, {"hand_R": (-0.15, 1.01, 0.25), "wrist_R": (0, 0, 42), **({"hand_L": (0.152, 1.015, 0.25), "wrist_L": (0, 0, 40)}
                                                                                       if hands == "both" else {})}))
    phrase(name, hands, "metaphoric", energy, "mid", "explaining how something goes, 'and so on'", dur, 0.3, 0.58, round(last + 0.05, 3), keys)


roll_phrase("g_roll_r", "right", "animated", 1.85)
roll_phrase("g_roll_both", "both", "animated", 1.9)

# ------------------------------------------------------------------------------ iconic / emblems
phrase("g_count_l", "left", "emblem", "animated", "mid", "listing points, 'first... second'", 1.9, 0.35, 0.55, 1.35, [
    (0.2, dict(hand_L=(0.101, 1.009, 0.206), wrist_L=(10, 0, 20))),
    (0.35, dict(hand_L=(0.13, 1.1, 0.27), wrist_L=(-15, 0, 5), fingers_L=(0.7, 0.05, 0.7, 0.7), fcurl_L=(0, 0.1, 0.15, 0.2))),
    (0.55, dict(hand_L=(0.13, 1.11, 0.27), wrist_L=(-18, 0, 0), **point("L", 1.0), d_head=(1, 3, 3))),
    (0.9, dict(hand_L=(0.132, 1.105, 0.27), wrist_L=(-16, 0, 0), fingers_L=(0.92, 0.12, 0.85, 0.5), fcurl_L=(-0.95, -0.9, 0.05, 0.08),
               d_head=(2, 3, 4))),  # two
    (1.35, dict(hand_L=(0.132, 1.1, 0.268), wrist_L=(-14, 0, 2), fingers_L=(0.9, 0.12, 0.85, 0.5), fcurl_L=(-0.9, -0.85, 0.05, 0.08),
                d_head=(1, 2, 3))),
    (1.65, dict(hand_L=(0.106, 1.009, 0.206), wrist_L=(0, 0, 30), fingers_L=SOFT)),
])
phrase("g_pinch_r", "right", "iconic", "calm", "mid", "something small, 'just a little bit'", 1.6, 0.4, 0.6, 1.05, [
    (0.22, dict(hand_R=(-0.112, 1.021, 0.213), wrist_R=(0, 0, 30))),
    (0.4, dict(hand_R=(-0.13, 1.085, 0.28), wrist_R=(-10, 0, 20), fingers_R=(0.8, 0.0, 0.6, 0.7), fcurl_R=(-0.3, 0.1, 0.1, 0.15))),
    (0.6, dict(hand_R=(-0.13, 1.09, 0.28), wrist_R=(-12, 0, 18), fingers_R=(0.8, 0.0, 0.8, 0.85), fcurl_R=(-0.2, 0.1, 0.1, 0.15),
               d_head=(1, -3, -6))),
    (0.8, dict(hand_R=(-0.131, 1.088, 0.279), wrist_R=(-11, 0, 18), fingers_R=(0.8, 0.0, 0.72, 0.78), fcurl_R=(-0.25, 0.1, 0.1, 0.15),
               d_head=(0.5, -3, -6))),
    (1.05, dict(hand_R=(-0.132, 1.085, 0.278), wrist_R=(-10, 0, 18), fingers_R=(0.8, 0.0, 0.8, 0.85), fcurl_R=(-0.2, 0.1, 0.1, 0.15),
                d_head=(0.5, -2, -5))),
    (1.35, dict(hand_R=(-0.112, 1.011, 0.208), wrist_R=(0, 0, 32), fingers_R=SOFT)),
])
POINT_R = [
    (0.3, dict(hand_R=(-0.15, 1.05, 0.23), wrist_R=(0, 0, 25), **point("R", 0.6))),
    (0.5, dict(hand_R=(-0.19, 1.1, 0.37), wrist_R=(-10, 0, 20), **point("R", 1.0), d_torso=(0, 0, -3), d_head=(0, -4, -2))),
    (0.66, dict(hand_R=(-0.188, 1.098, 0.362), wrist_R=(-6, 0, 22), **point("R", 1.0), d_torso=(0, 0, -2.5), d_head=(1, -4, -2))),
    (0.95, dict(hand_R=(-0.186, 1.094, 0.355), wrist_R=(-4, 0, 24), **point("R", 0.9), d_torso=(0, 0, -2), d_head=(0.5, -3, -2))),
    (1.22, dict(hand_R=(-0.122, 1.011, 0.218), wrist_R=(0, 0, 36), fingers_R=SOFT, fcurl_R=(-0.04, 0.02, 0.09, 0.17))),
]
phrase("g_point_r", "right", "deictic", "animated", "mid", "pointing at the listener or something out there, 'you', 'that one'",
       1.5, 0.3, 0.5, 0.95, POINT_R)
phrase(**mirrored("g_point_l", "left", "animated", POINT_R), kind="deictic", rest="mid",
       when="pointing at the listener or something out there, 'you', 'that one'", dur=1.5, prep_end=0.3, stroke=0.5, retract_start=0.95)
phrase("g_waveaway_r", "right", "emblem", "animated", "mid", "waving it off, 'nah', 'never mind'", 1.4, 0.3, 0.5, 0.8, [
    (0.3, dict(hand_R=(-0.11, 1.07, 0.26), wrist_R=(0, -15, -45), fingers_R=(0.25, 0.35, 0.2, 0.25), d_head=(0, 3, 1))),
    (0.5, dict(hand_R=(-0.25, 1.03, 0.245), wrist_R=(0, 18, -40), fingers_R=(0.2, 0.45, 0.15, 0.2), d_head=(1, -6, -3))),
    (0.62, dict(hand_R=(-0.26, 1.025, 0.24), wrist_R=(0, 10, -38), d_head=(0.5, -3, -3))),
    (0.8, dict(hand_R=(-0.255, 1.02, 0.24), wrist_R=(0, 6, -30), fingers_R=(0.25, 0.4, 0.2, 0.25), d_head=(0, -2, -2))),
    (1.1, dict(hand_R=(-0.142, 0.991, 0.203), wrist_R=(0, 0, 20))),
])
phrase("g_minishrug", "both", "emblem", "calm", "low", "a little 'I don't know', 'maybe'", 1.5, 0.3, 0.52, 0.95, [
    (0.3, dict(d_shoulder_L=(-2, 0), d_shoulder_R=(-2, 0), hand_L=(0.14, 0.857, 0.08), hand_R=(-0.141, 0.858, 0.09), wrist_L=(8, 0, 30),
               wrist_R=(8, 0, 32))),
    (0.52, dict(d_shoulder_L=(8, 0), d_shoulder_R=(9, 0), hand_L=(0.21, 0.93, 0.22), hand_R=(-0.21, 0.925, 0.22), wrist_L=(-10, 0, 70),
                wrist_R=(-10, 0, 72), fingers_L=PALM, fingers_R=PALM, d_head=(-2, 0, 6))),
    (0.75, dict(d_shoulder_L=(6, 0), d_shoulder_R=(7, 0), hand_L=(0.21, 0.925, 0.218), hand_R=(-0.21, 0.92, 0.218), wrist_L=(-8, 0, 68),
                wrist_R=(-8, 0, 70), fingers_L=PALM, fingers_R=PALM, d_head=(-1, 0, 5))),
    (0.95, dict(d_shoulder_L=(5, 0), d_shoulder_R=(6, 0), hand_L=(0.208, 0.92, 0.215), hand_R=(-0.208, 0.915, 0.215), wrist_L=(-6, 0, 64),
                wrist_R=(-6, 0, 66), fingers_L=PALM, fingers_R=PALM, d_head=(-1, 0, 4))),
    (1.25, dict(hand_L=(0.14, 0.877, 0.09), hand_R=(-0.141, 0.878, 0.1), wrist_L=(6, 0, 20), wrist_R=(6, 0, 22))),
])
phrase("g_clasp_excited", "both", "emblem", "excited", "mid", "delighted, 'oh I love that!'", 1.5, 0.25, 0.4, 0.95, hands_touch=True, keys=[
    (0.25, dict(hand_L=(0.06, 1.13, 0.28), hand_R=(-0.06, 1.13, 0.28), wrist_L=(-10, 0, 10), wrist_R=(-10, 0, 10), fingers_L=OPEN,
                fingers_R=OPEN, d_shoulder_L=(4, 0), d_shoulder_R=(4, 0), elbow_L=(0.22, 0.86, -0.22), elbow_R=(-0.2, 0.86, -0.22))),
    (0.4, dict(elbow_L=(0.24, 0.85, -0.2), elbow_R=(-0.22, 0.85, -0.2), hand_L=(0.026, 1.155, 0.29), hand_R=(-0.026, 1.15, 0.29), wrist_L=(-15, 0, 0), wrist_R=(-15, 0, 0),
               fingers_L=(0.55, 0.05, 0.4, 0.5), fingers_R=(0.55, 0.05, 0.4, 0.5), d_shoulder_L=(8, 0), d_shoulder_R=(8, 0), d_head=(-3, 0, 8),
               d_torso=(-2, 0, 0))),
    (0.55, dict(elbow_L=(0.24, 0.85, -0.2), elbow_R=(-0.22, 0.85, -0.2), hand_L=(0.026, 1.145, 0.288), hand_R=(-0.026, 1.14, 0.288), wrist_L=(-12, 0, 0), wrist_R=(-12, 0, 0),
                fingers_L=(0.55, 0.05, 0.4, 0.5), fingers_R=(0.55, 0.05, 0.4, 0.5), d_shoulder_L=(6, 0), d_shoulder_R=(6, 0), d_head=(0, 0, 9),
                d_torso=(0, 0, 0))),
    (0.72, dict(elbow_L=(0.24, 0.85, -0.2), elbow_R=(-0.22, 0.85, -0.2), hand_L=(0.026, 1.155, 0.29), hand_R=(-0.026, 1.15, 0.29), fingers_L=(0.55, 0.05, 0.4, 0.5), fingers_R=(0.55, 0.05, 0.4, 0.5),
                wrist_L=(-14, 0, 0), wrist_R=(-14, 0, 0), d_shoulder_L=(7, 0), d_shoulder_R=(7, 0), d_head=(-2, 0, 8), d_torso=(-1.5, 0, 0))),
    (0.95, dict(elbow_L=(0.24, 0.85, -0.2), elbow_R=(-0.22, 0.85, -0.2), hand_L=(0.028, 1.145, 0.285), hand_R=(-0.028, 1.14, 0.285), fingers_L=(0.5, 0.1, 0.4, 0.5), fingers_R=(0.5, 0.1, 0.4, 0.5),
                wrist_L=(-10, 0, 0), wrist_R=(-10, 0, 0), d_shoulder_L=(4, 0), d_shoulder_R=(4, 0), d_head=(-1, 0, 6))),
    (1.25, dict(hand_L=(0.12, 1.04, 0.25), hand_R=(-0.12, 1.035, 0.25), wrist_L=(0, 0, 25), wrist_R=(0, 0, 27))),
])
YOUKNOW_R = [
    (0.35, dict(hand_R=(-0.12, 1.02, 0.26), wrist_R=(5, 0, 45), fingers_R=(0.3, 0.3, 0.25, 0.3))),
    (0.55, dict(hand_R=(-0.11, 1.05, 0.33), wrist_R=(-22, 0, 66), fingers_R=PALM, fcurl_R=(0, 0, 0.05, 0.1), d_head=(3, -2, 5))),
    (0.78, dict(hand_R=(-0.112, 1.048, 0.328), wrist_R=(-20, 0, 64), fingers_R=PALM, d_head=(1, -2, 6))),
    (1.0, dict(hand_R=(-0.114, 1.045, 0.325), wrist_R=(-18, 0, 62), fingers_R=(0.2, 0.35, 0.18, 0.22), d_head=(0.5, -1, 5))),
    (1.3, dict(hand_R=(-0.112, 0.996, 0.218), wrist_R=(0, 0, 45))),
]
phrase("g_youknow_r", "right", "metaphoric", "animated", "mid", "checking in with the listener, 'you know?'", 1.6, 0.35, 0.55, 1.0, YOUKNOW_R)
phrase(**mirrored("g_youknow_l", "left", "animated", YOUKNOW_R), kind="metaphoric", rest="mid",
       when="checking in with the listener, 'you know?'", dur=1.6, prep_end=0.35, stroke=0.55, retract_start=1.0)
phrase("g_chest_r", "right", "deictic", "calm", "low", "talking about herself, 'I', 'for me'", 1.8, 0.4, 0.62, 1.1, [
    (0.22, dict(hand_R=(-0.141, 0.873, 0.075), wrist_R=(12, 0, 0))),
    (0.4, dict(hand_R=(-0.112, 0.991, 0.208), elbow_R=(-0.28, 0.84, -0.16), wrist_R=(5, 0, 5), fingers_R=(0.2, 0.3, 0.2, 0.25))),
    (0.62, dict(hand_R=(-0.065, 1.12, 0.16), elbow_R=(-0.27, 0.8, -0.08), wrist_R=(-20, 0, 0), fingers_R=(0.15, 0.3, 0.2, 0.2),
                fcurl_R=(0, 0, 0.05, 0.1), d_head=(3, 1, 3), d_torso=(-2, 0, 0))),
    (0.85, dict(hand_R=(-0.065, 1.118, 0.16), elbow_R=(-0.27, 0.8, -0.08), wrist_R=(-20, 0, 0), fingers_R=(0.15, 0.3, 0.2, 0.2),
                d_head=(0.5, 1, 4), d_torso=(-1.5, 0, 0))),
    (1.1, dict(hand_R=(-0.066, 1.115, 0.162), elbow_R=(-0.27, 0.8, -0.08), wrist_R=(-18, 0, 0), fingers_R=(0.18, 0.28, 0.2, 0.22),
               d_head=(0.5, 1, 3), d_torso=(-1, 0, 0))),
    (1.45, dict(hand_R=(-0.18, 0.93, 0.19), elbow_R=(-0.26, 0.84, -0.18), wrist_R=(8, 0, 5), fingers_R=(0.32, 0.22, 0.28, 0.36))),
], contacts=[("R", 0.62, 1.1, "torso", "palm", 0.005)])
phrase("g_chest_both", "both", "emblem", "excited", "mid", "moved, 'I love it', 'that means so much'", 1.7, 0.35, 0.55, 1.1, hands_touch=True, keys=[
    (0.35, dict(hand_L=(0.12, 1.08, 0.25), hand_R=(-0.12, 1.075, 0.25), elbow_L=(0.28, 0.84, -0.16), elbow_R=(-0.28, 0.84, -0.16),
                wrist_L=(0, 0, 10), wrist_R=(0, 0, 10))),
    (0.55, dict(hand_L=(0.07, 1.12, 0.16), hand_R=(-0.06, 1.13, 0.17), elbow_L=(0.27, 0.8, -0.08), elbow_R=(-0.27, 0.8, -0.08),
                wrist_L=(0, 0, 0), wrist_R=(0, 0, 0), fingers_L=(0.15, 0.3, 0.2, 0.2), fingers_R=(0.15, 0.3, 0.2, 0.2),
                d_shoulder_L=(6, 2), d_shoulder_R=(6, 2), d_head=(2, 0, 8), d_torso=(1, 0, 0))),
    (0.8, dict(hand_L=(0.07, 1.118, 0.16), hand_R=(-0.06, 1.128, 0.17), elbow_L=(0.27, 0.8, -0.08), elbow_R=(-0.27, 0.8, -0.08),
               wrist_L=(0, 0, 0), wrist_R=(0, 0, 0), fingers_L=(0.15, 0.3, 0.2, 0.2), fingers_R=(0.15, 0.3, 0.2, 0.2),
               d_shoulder_L=(4, 2), d_shoulder_R=(4, 2), d_head=(1, 0, 10), d_torso=(1.5, 0, 0))),
    (1.1, dict(hand_L=(0.07, 1.115, 0.16), hand_R=(-0.06, 1.125, 0.17), elbow_L=(0.27, 0.8, -0.08), elbow_R=(-0.27, 0.8, -0.08),
               wrist_L=(0, 0, 0), wrist_R=(0, 0, 0), fingers_L=(0.2, 0.28, 0.22, 0.25), fingers_R=(0.2, 0.28, 0.22, 0.25),
               d_shoulder_L=(3, 1), d_shoulder_R=(3, 1), d_head=(0.5, 0, 8), d_torso=(1, 0, 0))),
    (1.42, dict(hand_L=(0.101, 1.019, 0.206), hand_R=(-0.102, 1.016, 0.208), elbow_L=(0.26, 0.84, -0.18), elbow_R=(-0.26, 0.84, -0.18),
                wrist_L=(0, 0, 25), wrist_R=(0, 0, 27))),
], contacts=[("R", 0.55, 1.1, "torso", "palm", 0.005), ("L", 0.55, 1.1, "torso", "palm", 0.005)])
phrase("g_frame_both", "both", "iconic", "animated", "mid", "showing a size, 'about this big'", 1.7, 0.4, 0.6, 1.15, [
    (0.22, dict(hand_L=(0.11, 1.03, 0.25), hand_R=(-0.11, 1.025, 0.25), wrist_L=(0, 0, 20), wrist_R=(0, 0, 22))),
    (0.4, dict(hand_L=(0.155, 1.1, 0.28), hand_R=(-0.155, 1.095, 0.28), wrist_L=(-10, 0, -5), wrist_R=(-10, 0, -5),
               fingers_L=(0.12, 0.2, 0.1, 0.15), fingers_R=(0.12, 0.2, 0.1, 0.15), fcurl_L=ZERO4, fcurl_R=ZERO4)),
    (0.6, dict(hand_L=(0.19, 1.105, 0.28), hand_R=(-0.19, 1.1, 0.28), wrist_L=(-12, 0, -8), wrist_R=(-12, 0, -8),
               fingers_L=(0.1, 0.2, 0.1, 0.15), fingers_R=(0.1, 0.2, 0.1, 0.15), d_head=(1, 0, 3))),
    (0.8, dict(hand_L=(0.186, 1.1, 0.279), hand_R=(-0.186, 1.095, 0.279), wrist_L=(-10, 0, -6), wrist_R=(-10, 0, -6),
               fingers_L=(0.12, 0.2, 0.1, 0.15), fingers_R=(0.12, 0.2, 0.1, 0.15), d_head=(0.5, 0, 4))),
    (1.15, dict(hand_L=(0.184, 1.097, 0.278), hand_R=(-0.184, 1.092, 0.278), wrist_L=(-8, 0, -4), wrist_R=(-8, 0, -4),
                fingers_L=(0.15, 0.22, 0.12, 0.18), fingers_R=(0.15, 0.22, 0.12, 0.18), d_head=(0.5, 0, 3))),
    (1.45, dict(hand_L=(0.111, 1.009, 0.206), hand_R=(-0.112, 1.006, 0.208), wrist_L=(0, 0, 25), wrist_R=(0, 0, 27))),
])
phrase("g_sweep_both", "both", "metaphoric", "excited", "mid", "everything, 'all of it', 'the whole thing'", 1.6, 0.35, 0.62, 1.05, [
    (0.35, dict(hand_L=(0.05, 1.07, 0.3), hand_R=(-0.05, 1.065, 0.3), wrist_L=(10, 0, -30), wrist_R=(10, 0, -30),
                fingers_L=(0.2, 0.3, 0.15, 0.2), fingers_R=(0.2, 0.3, 0.15, 0.2), d_head=(2, 0, 0))),
    (0.5, dict(hand_L=(0.19, 1.06, 0.28), hand_R=(-0.19, 1.055, 0.28), wrist_L=(0, 0, -25), wrist_R=(0, 0, -25),
               fingers_L=(0.12, 0.5, 0.1, 0.15), fingers_R=(0.12, 0.5, 0.1, 0.15))),
    (0.62, dict(hand_L=(0.29, 1.04, 0.23), hand_R=(-0.29, 1.035, 0.23), wrist_L=(-8, 0, -20), wrist_R=(-8, 0, -20), fingers_L=OPEN,
                fingers_R=OPEN, fcurl_L=ZERO4, fcurl_R=ZERO4, d_torso=(-3, 0, 0), d_head=(-4, 0, 0), d_shoulder_L=(3, 0), d_shoulder_R=(3, 0))),
    (0.82, dict(hand_L=(0.292, 1.035, 0.228), hand_R=(-0.292, 1.03, 0.228), wrist_L=(-6, 0, -18), wrist_R=(-6, 0, -18), fingers_L=OPEN,
                fingers_R=OPEN, d_torso=(-2, 0, 0), d_head=(-2, 0, 0), d_shoulder_L=(2, 0), d_shoulder_R=(2, 0))),
    (1.05, dict(hand_L=(0.285, 1.03, 0.225), hand_R=(-0.285, 1.025, 0.225), wrist_L=(-4, 0, -10), wrist_R=(-4, 0, -10),
                fingers_L=(0.2, 0.45, 0.15, 0.2), fingers_R=(0.2, 0.45, 0.15, 0.2), d_torso=(-1, 0, 0))),
    (1.35, dict(hand_L=(0.141, 0.994, 0.201), hand_R=(-0.142, 0.991, 0.203), wrist_L=(0, 0, 25), wrist_R=(0, 0, 27))),
])


# ------------------------------------------------------------------------------ multi-stroke phrases
# Several beats chained without returning to rest (`strokes`, first = `stroke`), for fast speech.
def beats(side, rest, amp, strokes, dur, retract, lift=0.028, drop=0.032, fwd=0.02, prep_end=None):
    """Beat keys for one hand: a prep lift, then down on every stroke with a partial rise between (the
    rise peaks on the scheduler's hold point for that beat, see hold_points)."""
    holds = hold_points(prep_end if prep_end is not None else strokes[0] - 0.14, strokes[0], strokes)
    S = side
    hx, hy, hz = RESTS[rest][f"hand_{S}"]
    tw = RESTS[rest][f"wrist_{S}"][2]
    up = lambda k: {f"hand_{S}": (hx, hy + lift * amp * k, hz + fwd * 0.5 * amp), f"wrist_{S}": (-10 * amp, 0, tw)}  # noqa: E731
    down = {f"hand_{S}": (hx, hy - drop * amp, hz + fwd * amp), f"wrist_{S}": (9 * amp, 0, tw - 2)}
    keys = [(holds[0], up(1.0))]
    for i, t in enumerate(strokes):
        keys.append((t, dict(down)))
        if i + 1 < len(strokes):
            keys.append((holds[i + 1], up(0.6)))
    keys.append((retract, {f"hand_{S}": (hx, hy - drop * amp * 0.6, hz + fwd * amp)}))
    keys.append((round(retract + (dur - retract) * 0.55, 3), {f"hand_{S}": (hx, hy + 0.004, hz + 0.006)}))
    return keys


def both(ka, kb):
    return [(t, {**a, **b}) for (t, a), (_, b) in zip(ka, kb)]


ST = [0.34, 0.66, 0.98]
phrase("g_beats3_r", "right", "beat", "animated", "mid", "three quick emphases, 'one, two, three'", 1.55, 0.22, ST[0], 1.08,
       beats("R", "mid", 1.0, ST, 1.55, 1.08, prep_end=0.22), strokes=ST)
phrase("g_beats3_l", "left", "beat", "calm", "mid", "three soft emphases in a row", 1.55, 0.22, ST[0], 1.08,
       beats("L", "mid", 0.7, ST, 1.55, 1.08, prep_end=0.22), strokes=ST)
ST2 = [0.3, 0.64]
phrase("g_beats2_both", "both", "beat", "excited", "mid", "two strong emphases, 'so, so good'", 1.25, 0.18, ST2[0], 0.74,
       both(beats("R", "mid", 1.25, ST2, 1.25, 0.74, prep_end=0.18), beats("L", "mid", 1.2, ST2, 1.25, 0.74, prep_end=0.18)), strokes=ST2)
ST3 = [0.5, 0.9, 1.3]
LIST_UP = dict(hand_L=(0.11, 1.1, 0.24), wrist_L=(-15, 0, 5))
phrase("g_list3_l", "left", "emblem", "animated", "mid", "listing three things, 'this, that, and that'", 1.9, 0.38, ST3[0], 1.4, [
    (0.22, dict(hand_L=(0.12, 1.02, 0.22), wrist_L=(10, 0, 20))),
    (0.38, dict(LIST_UP, fingers_L=(0.75, 0.05, 0.7, 0.7), fcurl_L=(0, 0.1, 0.15, 0.2))),
    (ST3[0], dict(hand_L=(0.112, 1.095, 0.255), wrist_L=(-18, 0, 0), **point("L", 1.0), d_head=(1, 3, 3))),
    (0.78, dict(LIST_UP, **point("L", 1.0))),
    (ST3[1], dict(hand_L=(0.113, 1.093, 0.257), wrist_L=(-16, 0, 0), fingers_L=(0.92, 0.12, 0.85, 0.5),
                  fcurl_L=(-0.95, -0.9, 0.05, 0.08), d_head=(2, 3, 4))),
    (1.18, dict(LIST_UP, fingers_L=(0.9, 0.2, 0.85, 0.5), fcurl_L=(-0.95, -0.9, 0.05, 0.08))),
    (ST3[2], dict(hand_L=(0.114, 1.09, 0.258), wrist_L=(-14, 0, 2), fingers_L=(0.88, 0.3, 0.2, 0.5),
                  fcurl_L=(-0.95, -0.9, -0.85, 0.1), d_head=(2, 2, 3))),
    (1.62, dict(hand_L=(0.12, 1.0, 0.21), wrist_L=(0, 0, 30), fingers_L=SOFT)),
], strokes=ST3)

# ------------------------------------------------------------------------------ short beats (0.6-0.9 s)
def tap(name, side, energy, amp, dur, when):
    S = side
    hx, hy, hz = REST_MID[f"hand_{S}"]
    tw = REST_MID[f"wrist_{S}"][2]
    stroke = round(dur * 0.4, 3)
    keys = [(round(stroke - 0.12, 3), {f"hand_{S}": (hx, hy + 0.02 * amp, hz + 0.008), f"wrist_{S}": (-12 * amp, 0, tw)}),
            (stroke, {f"hand_{S}": (hx, hy - 0.024 * amp, hz + 0.018 * amp), f"wrist_{S}": (12 * amp, 0, tw - 2)}),
            (round(stroke + 0.14, 3), {f"hand_{S}": (hx, hy - 0.012 * amp, hz + 0.014 * amp), f"wrist_{S}": (6 * amp, 0, tw)})]
    return dict(name=name, hands={"L": "left", "R": "right"}[S], kind="beat", energy=energy, rest="mid", when=when, dur=dur,
                prep_end=round(stroke - 0.1, 3), stroke=stroke, retract_start=round(stroke + 0.14, 3), keys=keys)


phrase(**tap("g_tap_r", "R", "calm", 0.8, 0.7, "a light tap on a word"))
phrase(**tap("g_tap_l", "L", "animated", 1.0, 0.7, "a light tap on a word"))
_tb = tap("g_tap_both", "R", "excited", 1.2, 0.8, "a quick double-hand tap on a word")
_tl = tap("g_tap_both", "L", "excited", 1.15, 0.8, "")
phrase(**{**_tb, "hands": "both", "keys": [(t, {**a, **b}) for (t, a), (_, b) in zip(_tb["keys"], _tl["keys"])]})
phrase("g_flick_small_r", "right", "metaphoric", "animated", "mid", "a tiny 'whatever' flick", 0.85, 0.24, 0.34, 0.5, [
    (0.2, dict(hand_R=(-0.11, 1.0, 0.21), wrist_R=(18, 0, 30), fingers_R=(0.55, 0.05, 0.5, 0.5))),
    (0.34, dict(hand_R=(-0.16, 1.005, 0.215), wrist_R=(-15, -6, 50), fingers_R=(0.1, 0.5, 0.08, 0.12), fcurl_R=ZERO4)),
    (0.5, dict(hand_R=(-0.158, 1.0, 0.213), wrist_R=(-10, -4, 48), fingers_R=(0.18, 0.4, 0.15, 0.2))),
])
