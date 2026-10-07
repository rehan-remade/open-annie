"""Annie's clips as keyframe data for the control rig (channels: animate.py, poses: poses.py).

Every clip is a Seq of key poses. Non-loop clips start and end exactly on BASE (the idle base pose),
loops end on their first key. `lag` delays channel groups by n frames on interior keys (overlap and
follow-through: head, wrists and fingers trail the arms). `contacts` are intentional touches the
collision solver snaps onto the surface: (side, t0, t1, part, anchor, gap).

Style: a friendly, feminine young-adult anime character. Compact, graceful gestures; relaxed
contrapposto; soft elbows; anticipation -> action -> follow-through -> settle; never mirror-perfect.
The gesture phrase library (co-speech, layer "gesture") lives in phrases.py.
"""

from poses import BASE, CLASP, HOLD, LOOSE_FC, OPEN, REST_LOW, REST_MID, ZERO4, P, Seq, fists, heel, mirror, point

DESCRIPTION = (
    "Keyframed body motion for Annie, posed on an IK control rig (arm / leg IK, COG, spine bend, head aim, finger "
    "curl / spread controls) in Blender and baked to the humanoid bones: idles in a relaxed contrapposto, one-shot "
    "fidgets, listening loops, Jev's semantic gestures, talk-rest holds and a co-speech gesture phrase library "
    "(layer 'gesture', with prep / stroke / retract markers). Every clip is collision-checked on the deformed "
    "avatar mesh as shown (AvatarSample_B without the varsity jacket: hands, fingers and arms never inside the skin, crop top, "
    "skirt, thighs or hair; resting arms lightly touch the waist and skirt) and corrected "
    "by pushing the arm IK targets out, temporally smoothed. Gestures and fidgets start and end on the idle base pose; "
    "the dance is 120 bpm with its dips on 0.25 + 0.5 k s (demo/beat.py).")
RESTS_NOTE = {"low": "arms relaxed, hands resting on the skirt just in front of the hips (talk_calm_*)",
              "mid": "hands at waist height in front, forearms about level, elbows lightly on the waist (talk_animated_*, talk_excited_*)",
              "note": "gesture phrases start and end on the rest named by their `rest`; talk-rest loops hold it"}

LAG = {"head": 3, "fingers": 4, "wrist": 2}
CLIPS = []


def clip(name, dur, seq, loop=False, kind=None, when=None, lag=LAG, **extra):
    CLIPS.append({"name": name, "duration": dur, "loop": loop, "keys": seq.keys, "kind": kind, "when": when,
                  "lag": lag, **extra})


# ---------------------------------------------------------------------------------------- idles
# idle_a: settles into the left hip and breathes back out; hands trail the torso.
s = Seq()
s.k(0)
s.k(1.7, cog=(0.033, -0.024, 0.012), cog_rot=(1.0, 6.2, 5.6), torso=(1.4, 6.6, -6.6), head=(-1.5, -2.8, -4.6),
    fingers_L=(0.45, 0.12, 0.35, 0.45))
s.k(3.5, cog=(0.022, -0.02, 0.014), cog_rot=(1.3, 4.0, 3.7), torso=(2.1, 4.6, -5.0), head=(-0.6, -1.0, -2.4),
    hand_R=(-0.158, 0.85, 0.083), fingers_R=(0.41, 0.15, 0.3, 0.45), wrist_R=(10, -5, -9))
s.k(5.3, cog=(0.029, -0.023, 0.01), cog_rot=(0.8, 5.6, 5.0), torso=(1.2, 6.1, -6.4), head=(-2.0, -3.2, -3.8),
    hand_L=(0.154, 0.837, 0.075), fingers_L=(0.4, 0.13, 0.33, 0.45), hand_R=(-0.161, 0.845, 0.076), wrist_R=(12, -5, -12))
s.to(7.0, BASE)
clip("idle_a", 7.0, s, loop=True, kind="idle", when="calm standing idle",
     lag={"head": 6, "fingers": 9, "wrist": 5, "arms": 4})

# idle_b: weight passes to the right leg (left knee softens, heel just lifts) and back.
s = Seq()
s.k(0)
s.k(1.6, cog=(0.02, -0.026, 0.016), cog_rot=(1.0, 3.5, 3.0), torso=(1.6, 4.0, -4.5), head=(-1.2, -1.2, -2.2))  # gathers
s.k(3.0, cog=(-0.02, -0.022, 0.03), cog_rot=(0.6, -3.0, -4.2), torso=(1.6, -4.4, 3.4), head=(-1.0, 2.6, 3.2),
    hand_L=(0.139, 0.848, 0.081), hand_R=(-0.171, 0.84, 0.091), wrist_L=(10, -4, -12), **heel("L", 0.22))
s.k(4.9, cog=(-0.022, -0.023, 0.032), cog_rot=(0.7, -3.6, -4.7), torso=(1.3, -4.9, 3.7), head=(0.0, 1.6, 3.6),
    fingers_R=(0.48, 0.12, 0.33, 0.45), **heel("L", 0.25))
s.k(6.4, cog=(0.018, -0.025, 0.018), cog_rot=(1.0, 3.8, 3.2), torso=(1.5, 4.2, -4.6), head=(-1.4, -1.6, -2.6),
    hand_L=(0.151, 0.839, 0.071), hand_R=(-0.161, 0.845, 0.076), wrist_L=(10, -4, -16), fingers_R=(0.45, 0.14, 0.32, 0.45),
    **heel("L", 0))
s.to(8.0, BASE)
clip("idle_b", 8.0, s, loop=True, kind="idle", when="relaxed idle, shifting weight", feet_move=True,
     lag={"head": 7, "fingers": 9, "wrist": 5, "arms": 5})

# idle_c: hands loosely clasped low in front, a slow sway.
C0 = P(**CLASP)
s = Seq(C0)
s.k(0)
s.k(1.6, cog=(0.03, -0.024, 0.014), cog_rot=(1.2, 5.8, 5.4), torso=(2.0, 6.0, -6.2), head=(-1.0, -2.8, -4.4),
    hand_L=(0.033, 0.893, 0.265))
s.k(3.1, cog=(0.021, -0.02, 0.012), cog_rot=(0.8, 4.2, 3.6), torso=(2.4, 4.6, -5.0), head=(-2.0, -1.2, -2.2),
    fingers_R=(0.48, 0.1, 0.3, 0.5))
s.k(4.6, cog=(0.028, -0.023, 0.013), cog_rot=(1.1, 5.4, 4.9), torso=(1.6, 5.8, -6.4), head=(-1.2, -3.0, -3.6),
    hand_L=(0.037, 0.897, 0.26), fingers_R=(0.45, 0.1, 0.3, 0.5))
s.to(6.0, C0)
clip("idle_c", 6.0, s, loop=True, kind="idle", when="relaxed idle, hands loosely together in front", hands_touch=True,
     lag={"head": 6, "fingers": 9, "wrist": 5, "arms": 4})

# ---------------------------------------------------------------------------------------- fidgets
# One-shots the runtime plays every 6-12 s of idling. Start and end on BASE.
# Tuck hair behind the right ear: the hand rises along an arc, fingertips slide back over the hair.
EAR_R = dict(hand_R=(-0.13, 1.3, 0.07), elbow_R=(-0.45, 1.05, -0.05), wrist_R=(-35, 0, -60),
             fingers_R=(0.25, 0.15, 0.2, 0.25), fcurl_R=(0, 0, 0.08, 0.15), shoulder_R=(4, 3))
s = Seq()
s.k(0)
s.k(0.25, hand_R=(-0.156, 0.83, 0.061), shoulder_R=(-3, 1), head=(-1, -2, -2))  # tiny dip
s.k(0.6, hand_R=(-0.16, 1.12, 0.2), elbow_R=(-0.269, 0.95, -0.15), wrist_R=(0, 0, -40), fingers_R=(0.3, 0.2, 0.25, 0.3),
    head=(-1, -5, -6), torso=(1.5, 6.0, -7.5))
s.k(0.95, EAR_R, head=(0, -8, -10))
s.k(1.35, hand_R=(-0.128, 1.31, 0.035), wrist_R=(-30, 5, -70), head=(1, -9, -11))  # slides back behind the ear
s.k(1.6, hand_R=(-0.13, 1.3, 0.01), fingers_R=(0.35, 0.12, 0.25, 0.35), head=(0, -7, -9))
s.k(2.0, hand_R=(-0.17, 1.08, 0.13), elbow_R=(-0.1, 0.95, -0.28), wrist_R=(12, 0, -30), fingers_R=(0.42, 0.14, 0.32, 0.42),
    shoulder_R=(0, 1.5), head=(-1, -3, -5), torso=(1.5, 5.5, -6.2))
s.k(2.4, hand_R=(-0.158, 0.85, 0.081), wrist_R=(14, -5, -14), head=(-1.5, -2, -3.8))
s.to(2.8, BASE)
clip("fidget_hair", 2.8, s, kind="fidget", when="tucks her hair behind her ear",
     contacts=[("R", 0.95, 1.6, "head", "fingers", 0.004)], lag={"head": 4, "fingers": 5, "wrist": 3})

# Fiddle with the right wrist (was the jacket cuff; the name stays): the right forearm comes up, the
# left hand pinches the wrist and tugs twice, like turning a bracelet.
s = Seq()
s.k(0)
s.k(0.4, hand_R=(-0.07, 0.99, 0.28), elbow_R=(-0.262, 0.92, -0.12), wrist_R=(10, 0, -55), fingers_R=(0.4, 0.15, 0.35, 0.4),
    hand_L=(0.07, 0.95, 0.27), elbow_L=(0.05, 0.922, -0.268), wrist_L=(5, 5, 20), fingers_L=(0.5, 0.05, 0.6, 0.6),
    head=(8, -4, -2), torso=(3.0, 4.5, -6.0))
s.k(0.75, hand_L=(-0.005, 0.985, 0.29), fingers_L=(0.65, 0.0, 0.7, 0.7), fcurl_L=(-0.15, 0, 0.1, 0.2), head=(12, -6, -3))
s.k(1.05, hand_L=(0.025, 0.978, 0.285), hand_R=(-0.075, 0.985, 0.282))  # tug
s.k(1.3, hand_L=(0.0, 0.985, 0.29), hand_R=(-0.07, 0.99, 0.28))
s.k(1.55, hand_L=(0.028, 0.976, 0.285), hand_R=(-0.076, 0.984, 0.282), head=(11, -5, -2))  # tug
s.k(1.85, hand_L=(0.1, 0.93, 0.24), fingers_L=(0.4, 0.12, 0.35, 0.45), fcurl_L=LOOSE_FC, wrist_L=(8, 0, -10),
    hand_R=(-0.12, 0.95, 0.24), wrist_R=(10, 0, -30), head=(4, -3, -3))
s.k(2.25, hand_L=(0.144, 0.841, 0.071), hand_R=(-0.156, 0.848, 0.081), wrist_L=(10, -4, -16), wrist_R=(12, -5, -12),
    elbow_L=(0.072, 0.952, -0.303), elbow_R=(-0.06, 0.95, -0.3), fingers_R=(0.45, 0.14, 0.32, 0.45), head=(-1, -2, -3),
    torso=(1.6, 5.5, -6.0))
s.to(2.6, BASE)
clip("fidget_sleeve", 2.6, s, kind="fidget", when="fiddles with her wrist, as if turning a bracelet", lag={"head": 3, "fingers": 3, "wrist": 2})

# Rock heel-to-toe: the right foot steps in beside the left, hands clasp, up on the toes, back on the
# heels, and the foot steps back out.
FEET_TOGETHER = dict(foot_R=(-0.01, 0, 0.008, 0, -8, 0), foot_L=(0.012, 0, 0.0, 0, 5, 0))
s = Seq()
s.k(0)
s.k(0.35, foot_R=(-0.016, 0.03, 0.03, -5, -12, 0), cog=(0.03, -0.02, 0.008))  # right foot lifts
s.k(0.6, FEET_TOGETHER, CLASP, cog=(0.008, -0.018, 0.004), cog_rot=(0.5, 1.5, 1.0), torso=(1.5, 1.5, -1.5),
    head=(-1, -1, -2))
s.k(1.0, heel("L", 0.7, P(**FEET_TOGETHER)), heel("R", 0.7, P(**FEET_TOGETHER)), cog=(0.008, 0.008, 0.012),
    torso=(-1.0, 1.5, -1.5), head=(-3, -1, -3))  # up on the toes
s.k(1.35, heel("L", 0, P(**FEET_TOGETHER)), heel("R", 0, P(**FEET_TOGETHER)), cog=(0.008, -0.02, 0.002),
    head=(0, -1, -2))
s.k(1.7, foot_L=(0.012, 0.006, -0.004, -9, 5, 0), foot_R=(-0.01, 0.006, 0.004, -9, -8, 0), cog=(0.008, -0.022, -0.012),
    torso=(3.0, 1.5, -1.5), head=(2, -1, -1))  # back on the heels, toes up
s.k(2.05, FEET_TOGETHER, cog=(0.012, -0.02, 0.004), torso=(1.5, 2.5, -2.5), head=(-1, -1.5, -2.5))
s.k(2.4, foot_R=(-0.02, 0.028, 0.035, -4, -14, 0), cog=(0.028, -0.021, 0.01))
s.k(2.7, foot_R=BASE["foot_R"], cog=BASE["cog"], hand_L=(0.156, 0.841, 0.094), hand_R=(-0.146, 0.85, 0.111),
    wrist_L=(10, -4, -10), wrist_R=(12, -5, -8), fingers_L=(0.4, 0.12, 0.35, 0.45), fingers_R=(0.45, 0.14, 0.32, 0.45),
    elbow_L=(0.072, 0.952, -0.303), elbow_R=(-0.06, 0.95, -0.3), fcurl_L=(-0.08, 0.0, 0.06, 0.14),
    fcurl_R=(-0.05, 0.02, 0.08, 0.16), torso=(1.5, 5.0, -5.5), cog_rot=(1.0, 4.5, 4.0))
s.to(3.2, BASE)
clip("fidget_rock", 3.2, s, kind="fidget", when="rocks on her heels", feet_move=True, hands_touch=True, lag={"head": 4, "fingers": 5, "wrist": 3})

# Clasp -> unclasp: hands meet in front, a small squeeze, open and relax.
s = Seq()
s.k(0)
s.k(0.25, hand_L=(0.156, 0.846, 0.104), hand_R=(-0.146, 0.855, 0.121))
s.k(0.65, CLASP, head=(1, -1, -2), torso=(2.0, 5.0, -5.5))
s.k(1.05, hand_L=(0.03, 0.905, 0.268), hand_R=(-0.035, 0.89, 0.262), wrist_L=(22, -5, -45), wrist_R=(26, 5, -22),
    fingers_L=(0.4, 0.1, 0.35, 0.55), fingers_R=(0.55, 0.1, 0.35, 0.55), head=(3, -2, -3))  # squeeze
s.k(1.45, CLASP, head=(0, -1, -2))
s.k(1.8, hand_L=(0.13, 0.9, 0.22), hand_R=(-0.13, 0.89, 0.22), wrist_L=(0, 0, 10), wrist_R=(0, 0, 12),
    fingers_L=(0.12, 0.45, 0.1, 0.15), fingers_R=(0.15, 0.45, 0.12, 0.15), fcurl_L=ZERO4, fcurl_R=ZERO4,
    elbow_L=(0.052, 0.942, -0.333), elbow_R=(-0.06, 0.95, -0.3))  # fingers spread open
s.k(2.15, hand_L=(0.144, 0.841, 0.076), hand_R=(-0.158, 0.848, 0.081), wrist_L=(10, -4, -16), wrist_R=(12, -5, -12),
    fingers_L=(0.4, 0.12, 0.35, 0.45), fingers_R=(0.43, 0.14, 0.32, 0.45), fcurl_L=(-0.08, 0.0, 0.06, 0.14),
    fcurl_R=(-0.05, 0.02, 0.08, 0.16), elbow_L=(0.072, 0.952, -0.303), torso=BASE["torso"], head=(-1.5, -2, -3))
s.to(2.6, BASE)
clip("fidget_clasp", 2.6, s, kind="fidget", when="clasps and unclasps her hands", hands_touch=True, lag={"head": 4, "fingers": 6, "wrist": 3})

# Glance aside: eyes and head lead to her right, the body follows with a small weight shift, back.
s = Seq()
s.k(0)
s.k(0.35, head=(-2.5, -24, -5))  # head leads
s.k(0.7, head=(-3, -32, -7), torso=(1.5, 4.0, -12.0), cog=(0.018, -0.022, 0.014), cog_rot=(1.0, 3.0, 3.4),
    hand_R=(-0.156, 0.845, 0.086))
s.k(1.5, head=(-1.5, -30, -5.5), torso=(1.4, 3.6, -12.5))
s.k(1.85, head=(-2, -6, -3), torso=(1.5, 5.0, -7.0))  # head returns first
s.k(2.3, head=(-1.5, -1, -3.2), torso=(1.5, 5.6, -5.6), cog=(0.027, -0.022, 0.012), cog_rot=(1.0, 5.2, 4.7))
s.to(3.0, BASE)
clip("fidget_glance", 3.0, s, kind="fidget", when="glances aside for a moment", lag={"head": 0, "fingers": 6, "wrist": 4, "torso": 3})

# Small stretch: hands meet, reach up over the head, a lean back up on the toes, arms float down.
UP = dict(hand_L=(0.29, 1.575, 0.11), hand_R=(-0.29, 1.575, 0.11), elbow_L=(0.7, 1.35, -0.1), elbow_R=(-0.7, 1.35, -0.1),
          wrist_L=(-40, 0, 60), wrist_R=(-40, 0, 60), fingers_L=(0.35, 0.05, 0.3, 0.5), fingers_R=(0.35, 0.05, 0.3, 0.5),
          fcurl_L=ZERO4, fcurl_R=ZERO4, shoulder_L=(10, 0), shoulder_R=(10, 0))
s = Seq()
s.k(0)
s.k(0.4, hand_L=(0.08, 1.05, 0.25), hand_R=(-0.08, 1.05, 0.25), elbow_L=(0.244, 0.95, -0.1), elbow_R=(-0.244, 0.95, -0.1),
    wrist_L=(0, 0, 0), wrist_R=(0, 0, 0), head=(-2, -1, -2), torso=(1.5, 3.0, -3.0), cog=(0.012, -0.024, 0.008))
s.k(1.0, UP, torso=(-4, 1.0, -1.0), head=(-9, 0, -2), cog=(0.01, 0.0, -0.006), cog_rot=(0, 1, 1),
    **heel("L", 0.35), **heel("R", 0.35))
s.k(1.5, torso=(-6, 3.0, -1.0), head=(-10, 2, 3), hand_L=(0.3, 1.59, 0.1), hand_R=(-0.29, 1.585, 0.1))  # lean (elbows soft, never locked)
s.k(1.95, hand_L=(0.33, 1.45, 0.12), hand_R=(-0.33, 1.44, 0.12), elbow_L=(0.5, 1.2, -0.1), elbow_R=(-0.5, 1.2, -0.1),
    wrist_L=(-10, 0, 10), wrist_R=(-10, 0, 10), fingers_L=(0.25, 0.5, 0.2, 0.3), fingers_R=(0.25, 0.5, 0.2, 0.3),
    torso=(-2, 3.0, -3.0), head=(-4, -1, -2), shoulder_L=(4, 0), shoulder_R=(4, 0), **heel("L", 0), **heel("R", 0),
    cog=(0.02, -0.022, 0.01))  # arms float down and out
s.k(2.55, hand_L=(0.169, 0.876, 0.081), hand_R=(-0.186, 0.88, 0.091), elbow_L=(0.072, 0.952, -0.303), elbow_R=(-0.06, 0.95, -0.3),
    wrist_L=(8, -4, -10), wrist_R=(10, -5, -8), fingers_L=(0.4, 0.14, 0.35, 0.45), fingers_R=(0.43, 0.14, 0.32, 0.45),
    shoulder_L=(-3, 1), shoulder_R=(-2.5, 1.5), torso=(1.5, 5.0, -5.5), head=(-1, -2, -3.5))
s.to(3.2, BASE)
clip("fidget_stretch", 3.2, s, kind="fidget", when="does a small stretch", feet_move=True, hands_touch=True, lag={"head": 4, "fingers": 5, "wrist": 3})

# Hands on hips: a confident little pose, weight into one hip, then release.
HIPS = dict(hand_L=(0.125, 0.975, 0.03), hand_R=(-0.12, 0.98, 0.035), elbow_L=(0.5, 1.0, -0.15), elbow_R=(-0.5, 1.0, -0.15),
            wrist_L=(-25, 0, -70), wrist_R=(-25, 0, -70), fingers_L=(0.25, 0.05, 0.3, 0.3), fingers_R=(0.25, 0.05, 0.3, 0.3),
            fcurl_L=(0, 0.02, 0.05, 0.1), fcurl_R=(0, 0.02, 0.05, 0.1))
s = Seq()
s.k(0)
s.k(0.3, shoulder_L=(-2, 1), shoulder_R=(-2, 1), cog=(0.022, -0.026, 0.012), head=(1, -2, -2))  # tiny dip
s.k(0.7, HIPS, cog=(0.034, -0.022, 0.012), cog_rot=(1.0, 6.5, 6.0), torso=(0.5, 7.0, -7.0), head=(-2, -4, -7),
    shoulder_L=(2, -2), shoulder_R=(3, -2))
s.k(1.3, cog=(0.036, -0.023, 0.012), cog_rot=(1.0, 7.0, 6.5), head=(-3, -5, -9), hand_L=(0.126, 0.973, 0.028))
s.k(1.8, cog=(0.032, -0.022, 0.012), cog_rot=(1.0, 6.0, 5.5), head=(-2, -3, -7), torso=(0.8, 6.5, -6.5))
s.k(2.3, hand_L=(0.163, 0.865, 0.107), hand_R=(-0.161, 0.869, 0.108), elbow_L=(0.092, 0.952, -0.283), elbow_R=(-0.08, 0.95, -0.28),
    wrist_L=(8, -3, -12), wrist_R=(10, -4, -10), fingers_L=(0.4, 0.12, 0.35, 0.45), fingers_R=(0.43, 0.14, 0.32, 0.45),
    fcurl_L=(-0.08, 0.0, 0.06, 0.14), fcurl_R=(-0.05, 0.02, 0.08, 0.16), shoulder_L=(-2, 1), shoulder_R=(-2, 1.5),
    torso=BASE["torso"], head=(-1.5, -2, -3.5), cog=BASE["cog"], cog_rot=BASE["cog_rot"])
s.to(2.8, BASE)
clip("fidget_hips", 2.8, s, kind="fidget", when="rests her hands on her hips",
     contacts=[("L", 0.7, 1.8, "torso", "palm", 0.003), ("R", 0.7, 1.8, "torso", "palm", 0.003)],
     lag={"head": 4, "fingers": 4, "wrist": 3})

# ---------------------------------------------------------------------------------------- listening
# listen_a: right hand holding the left forearm, leaning in, head tilted, two small nods.
L0 = P(**HOLD, torso=(5.0, 4.0, -5.0), head=(3.0, -1.0, 6.0))
s = Seq(L0)
s.k(0)
s.k(1.5, head=(7.0, -1.5, 7.0), torso=(5.5, 3.6, -5.2))  # nod
s.k(1.95, head=(2.5, -1.0, 6.5))
s.k(3.4, head=(3.5, 1.5, 8.0), torso=(5.2, 4.6, -4.6), cog=(0.03, -0.024, 0.016), cog_rot=(1.2, 5.6, 5.2),
    hand_R=(0.003, 0.925, 0.293))
s.k(4.7, head=(7.5, 1.0, 7.5))  # nod
s.k(5.2, head=(3.0, 0.5, 6.8))
s.k(6.2, head=(3.0, -0.8, 6.0), torso=(5.0, 4.0, -5.0), cog=BASE["cog"], cog_rot=BASE["cog_rot"], hand_R=HOLD["hand_R"])
s.to(7.0, L0)
clip("listen_a", 7.0, s, loop=True, kind="listen", when="attentive listening", hands_touch=True, lag={"head": 3, "fingers": 8, "wrist": 4, "arms": 4})

# listen_b: hands clasped low, leaning in, head tilted to the other side, one nod, a slow tilt change.
LB = P(**CLASP, torso=(6.0, 4.5, -4.0), head=(3.0, 1.0, -6.0), cog=(0.02, -0.022, 0.018))
s = Seq(LB)
s.k(0)
s.k(1.8, head=(4.0, -1.5, -8.0), torso=(6.4, 5.2, -4.6), cog_rot=(1.2, 5.5, 5.0))
s.k(3.3, head=(8.0, -1.5, -8.0))  # nod
s.k(3.8, head=(3.5, -1.0, -7.5))
s.k(5.3, head=(3.0, 2.0, -4.0), torso=(5.6, 4.0, -3.6), cog_rot=(0.9, 4.6, 4.2), hand_L=(0.033, 0.893, 0.266))
s.to(7.0, LB)
clip("listen_b", 7.0, s, loop=True, kind="listen", when="interested listening, hands together, leaning in a little", hands_touch=True,
     lag={"head": 3, "fingers": 8, "wrist": 4, "arms": 4})

# ---------------------------------------------------------------------------------------- gestures
# wave: anticipation, the hand arcs up in front, waves from the elbow, fingers flutter behind it.
s = Seq()
s.k(0)
s.k(0.16, hand_R=(-0.151, 0.82, 0.051), shoulder_R=(-4, 1), wrist_R=(18, -5, -18), cog=(0.03, -0.024, 0.012))
s.k(0.4, hand_R=(-0.17, 1.08, 0.25), elbow_R=(-0.23, 0.88, -0.3), wrist_R=(-5, 0, 10), fingers_R=(0.25, 0.4, 0.2, 0.25),
    head=(-2, -4, -6), torso=(1.0, 7.0, -7.0), hand_L=(0.144, 0.846, 0.081))
s.k(0.62, hand_R=(-0.2, 1.38, 0.15), elbow_R=(-0.269, 0.9, 0.02), wrist_R=(-8, -4, 0), fingers_R=OPEN, fcurl_R=ZERO4,
    shoulder_R=(4, 0), head=(-3, -6, -8), torso=(0.5, 7.5, -7.5))
for i, t in enumerate((0.82, 1.02, 1.22, 1.42, 1.62)):
    out = i % 2 == 0
    s.k(t, hand_R=(-0.268, 1.355, 0.13) if out else (-0.196, 1.37, 0.155), wrist_R=(-8, 14 if out else -14, 0))
s.k(1.88, hand_R=(-0.21, 1.34, 0.16), wrist_R=(0, 0, 0), fingers_R=(0.2, 0.5, 0.15, 0.2), head=(-2, -4, -6))
s.k(2.18, hand_R=(-0.2, 1.0, 0.2), elbow_R=(-0.04, 0.9, -0.32), wrist_R=(12, 0, -5), fingers_R=(0.35, 0.25, 0.25, 0.35),
    shoulder_R=(0, 1.5), head=(-1.5, -2, -4), torso=(1.4, 5.8, -6.2))
s.to(2.5, BASE)
clip("wave", 2.5, s, kind="gesture", when="greeting hello or goodbye with a raised hand")

# clap: hands meet in front of the chest a little to her left, four claps with a bounce, a held beat.
CLAP_OPEN = dict(hand_L=(0.13, 1.12, 0.29), hand_R=(-0.08, 1.13, 0.3))
CLAP_HIT = dict(hand_L=(0.057, 1.125, 0.305), hand_R=(0.005, 1.13, 0.305))
s = Seq()
s.k(0)
s.k(0.22, hand_L=(0.2, 0.87, 0.14), hand_R=(-0.2, 0.87, 0.14), shoulder_L=(3, 0), shoulder_R=(3, 0), cog=(0.026, -0.027, 0.012))
s.k(0.45, CLAP_OPEN, elbow_L=(0.248, 0.86, -0.12), elbow_R=(-0.248, 0.86, -0.12), wrist_L=(-20, 0, 0), wrist_R=(-20, 0, 0),
    fingers_L=(0.1, 0.1, 0.1, 0.2), fingers_R=(0.1, 0.1, 0.1, 0.2), fcurl_L=(0, 0, 0.05, 0.1), fcurl_R=(0, 0, 0.05, 0.1),
    head=(-3, 1, 5), torso=(0, 5, -4), shoulder_L=(5, 0), shoulder_R=(5, 0))
for i, t in enumerate((0.6, 0.9, 1.2, 1.5)):
    s.k(t, CLAP_HIT, torso=(1.5, 5, -4), cog=(0.026, -0.03, 0.012), head=(-1, 1, 5))
    if t < 1.5:
        s.k(t + 0.15, CLAP_OPEN, torso=(0, 5, -4), cog=(0.026, -0.022, 0.012), head=(-3, 1, 6))
s.k(1.72, hand_L=(0.052, 1.12, 0.3), hand_R=(0.0, 1.125, 0.3), head=(-2, 1, 7), torso=(1, 5, -4))  # hold together, cute
s.k(2.02, hand_L=(0.16, 0.92, 0.2), hand_R=(-0.16, 0.92, 0.2), elbow_L=(0.052, 0.922, -0.333), elbow_R=(-0.06, 0.93, -0.3),
    wrist_L=(5, 0, -5), wrist_R=(5, 0, -5), fingers_L=(0.35, 0.15, 0.3, 0.4), fingers_R=(0.35, 0.15, 0.3, 0.4),
    shoulder_L=(-1, 1), shoulder_R=(-1, 1), head=(-1.5, -1, 0), torso=BASE["torso"])
s.to(2.3, BASE)
clip("clap", 2.3, s, kind="gesture", when="applause, great news, celebrating what someone said")

# shrug: a dip, shoulders pop up with palms turning up, head tilts, hold, settle.
SHRUG = dict(hand_L=(0.235, 1.0, 0.24), hand_R=(-0.225, 1.02, 0.24), elbow_L=(0.248, 0.86, -0.25), elbow_R=(-0.248, 0.86, -0.25),
             wrist_L=(-15, 0, 85), wrist_R=(-15, 0, 85), shoulder_L=(12, 0), shoulder_R=(14, 0),
             fingers_L=(0.15, 0.5, 0.1, 0.0), fingers_R=(0.15, 0.5, 0.1, 0.0), fcurl_L=(0, 0, 0.05, 0.1), fcurl_R=(0, 0, 0.05, 0.1),
             head=(-3, 1, 9), torso=(-2, 5, -5))
s = Seq()
s.k(0)
s.k(0.25, shoulder_L=(-3, 1), shoulder_R=(-3, 1), cog=(0.026, -0.027, 0.012), head=(1, -2, -1), hand_L=(0.144, 0.826, 0.081),
    hand_R=(-0.156, 0.83, 0.091), wrist_L=(5, 0, 20), wrist_R=(5, 0, 20))
s.k(0.55, SHRUG, cog=(0.026, -0.018, 0.012))
s.k(0.78, shoulder_L=(9, 0), shoulder_R=(11, 0), head=(-2, 1, 8), hand_L=(0.23, 0.99, 0.24), hand_R=(-0.22, 1.005, 0.24),
    cog=(0.026, -0.021, 0.012))
s.k(1.15, shoulder_L=(8, 0), shoulder_R=(10, 0), head=(-1, 0, 7), hand_L=(0.225, 0.98, 0.23), hand_R=(-0.215, 0.995, 0.23))
s.k(1.5, hand_L=(0.166, 0.851, 0.104), hand_R=(-0.156, 0.86, 0.121), wrist_L=(8, 0, 5), wrist_R=(8, 0, 5), shoulder_L=(-2, 1),
    shoulder_R=(-2, 1.5), fingers_L=(0.35, 0.2, 0.3, 0.4), fingers_R=(0.38, 0.2, 0.3, 0.4), head=(-1.5, -1.5, -1),
    torso=BASE["torso"], elbow_L=(0.072, 0.952, -0.303), elbow_R=(-0.06, 0.95, -0.3))
s.to(1.8, BASE)
clip("shrug", 1.8, s, kind="gesture", when="not knowing, 'who knows', playful 'oh well'")

# think: index finger to the chin, head tilts and turns, eyes up; the left forearm lies across the
# bare midriff and supports the right elbow; the finger taps twice.
THINK = dict(hand_R=(-0.03, 1.225, 0.16), elbow_R=(-0.244, 0.9, 0.2), wrist_R=(-25, 0, 20), **point("R", 0.85),
             hand_L=(-0.08, 1.0, 0.14), elbow_L=(0.248, 0.92, -0.05), wrist_L=(10, 0, 20), fingers_L=(0.4, 0.1, 0.3, 0.45),
             head=(-4, 10, 7), torso=(1, 4, -8), cog=(0.03, -0.022, 0.012))
s = Seq()
s.k(0)
s.k(0.22, head=(-3, -3, -3), hand_R=(-0.151, 0.83, 0.071))
s.k(0.5, hand_R=(-0.07, 1.2, 0.24), elbow_R=(-0.38, 0.82, -0.02), wrist_R=(8, 0, 25), fingers_R=(0.5, 0.05, 0.5, 0.5),
    hand_L=(0.0, 0.98, 0.24), elbow_L=(0.062, 0.922, -0.253))
s.k(0.75, THINK)
s.k(1.25, head=(-6, 13, 9), fcurl_R=(-0.6, 0.05, 0.05, 0.1))
s.k(1.45, fcurl_R=(-0.8, 0.05, 0.05, 0.1))  # tap
s.k(1.65, fcurl_R=(-0.6, 0.05, 0.05, 0.1))
s.k(1.85, fcurl_R=(-0.8, 0.05, 0.05, 0.1), head=(-5, 12, 10))  # tap
s.k(2.4, head=(-3, 7, 6), torso=(1.4, 4.5, -7.5))
s.k(2.72, hand_R=(-0.14, 1.0, 0.2), elbow_R=(-0.36, 0.84, -0.08), wrist_R=(10, 0, 0), fingers_R=(0.4, 0.15, 0.3, 0.42),
    fcurl_R=(-0.05, 0.02, 0.08, 0.16), hand_L=(0.13, 0.9, 0.19), elbow_L=(0.052, 0.942, -0.313), head=(-1.5, 1, -1),
    torso=BASE["torso"], cog=BASE["cog"])
s.to(3.2, BASE)
clip("think", 3.2, s, kind="gesture", when="pondering, working out an answer, explaining how something works",
     contacts=[("R", 0.8, 2.3, "head", "index", 0.003), ("L", 0.8, 2.3, "torso", "forearm", 0.004)])

# laugh: reads from the front. Inhale (head back), a hand up in front of her mouth (a few cm off: the
# stage's head map counts the hair around the face as head) as she doubles a little, the other on
# her tummy, shoulders bounce with the ha-ha-ha, head tips back then forward, recover.
MOUTH = dict(hand_R=(-0.02, 1.275, 0.168), elbow_R=(-0.26, 0.92, 0.26), wrist_R=(-5, 0, -25), fingers_R=(0.32, 0.1, 0.22, 0.3),
             fcurl_R=(0, 0.02, 0.06, 0.12), hand_L=(0.07, 1.0, 0.16), elbow_L=(0.3, 0.8, 0.0), wrist_L=(12, 0, -20),
             fingers_L=(0.35, 0.12, 0.3, 0.4))
s = Seq()
s.k(0)
s.k(0.16, torso=(-3, 5, -5), head=(-9, -2, -2), shoulder_L=(4, 0), shoulder_R=(4, 0))  # inhale, head back
s.k(0.34, hand_R=(-0.13, 1.03, 0.2), elbow_R=(-0.38, 0.8, -0.04), wrist_R=(0, 0, -10), hand_L=(0.14, 0.93, 0.16),
    elbow_L=(0.38, 0.8, -0.04), wrist_L=(10, 0, -10))  # the elbows swing out on the way up
s.k(0.52, MOUTH, torso=(9, 5, -5), head=(8, -3, 6), cog=(0.026, -0.03, 0.012))
for i, t in enumerate((0.58, 0.74, 0.9, 1.06, 1.22)):
    up = i % 2 == 0
    a = 1 - 0.14 * i
    s.k(t, torso=((4 if up else 12) * a, 5, -5), shoulder_L=((10 if up else 0) * a, 0), shoulder_R=((9 if up else 0) * a, 0),
        head=((-6 if up else 9) * a, -3, 6), cog=(0.026, -0.022 if up else -0.032, 0.012))
s.k(1.45, torso=(3, 5.5, -5.5), head=(-1, -2, 5), hand_R=(-0.12, 1.1, 0.22), wrist_R=(5, 0, -10), shoulder_L=(1, 0),
    shoulder_R=(1, 0), elbow_R=(-0.38, 0.82, -0.04), elbow_L=(0.38, 0.82, -0.04))
s.k(1.68, hand_R=(-0.165, 0.866, 0.113), hand_L=(0.156, 0.851, 0.104), elbow_L=(0.072, 0.952, -0.303), elbow_R=(-0.06, 0.95, -0.3),
    wrist_R=(10, 0, -10), wrist_L=(8, 0, -12), fingers_R=(0.42, 0.15, 0.3, 0.42), head=(-1.5, -2, 0), torso=BASE["torso"])
s.to(1.9, BASE)
clip("laugh", 1.9, s, kind="gesture", when="laughing out loud at something funny",
     contacts=[("L", 0.5, 1.25, "torso", "palm", 0.006)])

# sad_slump: a breath in, then everything sinks: head down, shoulders forward, arms limp, knees give.
SAD = dict(hand_L=(0.15, 0.86, 0.2), hand_R=(-0.15, 0.855, 0.2), elbow_L=(0.25, 1.0, -0.05), elbow_R=(-0.25, 1.0, -0.05),
           torso=(12, 3, -3), head=(22, -1, -5), shoulder_L=(-5, 8), shoulder_R=(-5, 8), cog=(0.02, -0.05, 0.008),
           cog_rot=(3, 3, 3), fingers_L=(0.25, 0.05, 0.25, 0.35), fingers_R=(0.25, 0.05, 0.25, 0.35), wrist_L=(20, 0, -25),
           wrist_R=(20, 0, -25))
s = Seq()
s.k(0)
s.k(0.5, torso=(-1.5, 5, -5), shoulder_L=(5, 0), shoulder_R=(5, 0), head=(-4, -2, -2), cog=(0.026, -0.014, 0.012))  # breath in
s.k(0.9, hand_L=(0.23, 0.86, 0.2), hand_R=(-0.22, 0.86, 0.195), torso=(5, 4, -4), head=(8, -1, -4), shoulder_L=(0, 4),
    shoulder_R=(0, 4), cog=(0.023, -0.035, 0.01))  # hands swing forward, clear of the skirt, as she deflates
s.k(1.3, SAD)
s.k(2.35, torso=(14, 3.5, -3), head=(24, -1, -6), cog=(0.02, -0.055, 0.006), hand_R=(-0.148, 0.85, 0.21))
s.k(2.9, torso=(7, 4, -4), head=(10, -1.5, -4), shoulder_L=(-3, 4), shoulder_R=(-3, 4), cog=(0.024, -0.035, 0.01),
    hand_L=(0.245, 0.835, 0.2), hand_R=(-0.225, 0.835, 0.19))
s.to(3.4, BASE)
clip("sad_slump", 3.4, s, kind="gesture", when="deflated by bad news, grief, feeling low", lag={"head": 5, "fingers": 6, "wrist": 3})

# surprised_recoil: fast. Hands snap up near the chest, palms out, torso and head back, knees give.
RECOIL = dict(hand_L=(0.12, 1.2, 0.25), hand_R=(-0.115, 1.19, 0.25), elbow_L=(0.248, 0.88, -0.08), elbow_R=(-0.248, 0.88, -0.08),
              wrist_L=(-45, 0, 0), wrist_R=(-45, 0, 0), fingers_L=(0.08, 0.9, 0.1, 0.1), fingers_R=(0.08, 0.9, 0.1, 0.1),
              fcurl_L=ZERO4, fcurl_R=ZERO4, torso=(-9, 3, -3), head=(-9, 0, 0), cog=(0.02, -0.035, -0.03),
              shoulder_L=(12, 0), shoulder_R=(12, 0))
s = Seq()
s.k(0)
s.k(0.2, RECOIL, torso=(-11, 3, -3), head=(-11, 1, -2), shoulder_L=(14, 0), shoulder_R=(14, 0),
    hand_L=(0.125, 1.23, 0.25), hand_R=(-0.11, 1.215, 0.255))
s.k(0.3, RECOIL, head=(-8, 1, -2))
s.k(0.85, torso=(-6, 3, -3), head=(-5, 0, -1), hand_L=(0.13, 1.17, 0.24), hand_R=(-0.13, 1.16, 0.24), shoulder_L=(8, 0),
    shoulder_R=(8, 0))
s.k(1.15, hand_L=(0.16, 0.97, 0.2), hand_R=(-0.16, 0.96, 0.2), wrist_L=(5, 0, 0), wrist_R=(5, 0, 0), elbow_L=(0.052, 0.922, -0.333),
    elbow_R=(-0.06, 0.93, -0.3), fingers_L=(0.3, 0.3, 0.25, 0.35), fingers_R=(0.3, 0.3, 0.25, 0.35), torso=(-1, 4, -4),
    head=(-2, -1, -1), shoulder_L=(1, 0), shoulder_R=(1, 0), cog=(0.024, -0.024, 0.0))
s.to(1.5, BASE)
clip("surprised_recoil", 1.5, s, kind="gesture", when="startled, taken aback, interrupted", lag={"head": 2, "fingers": 2, "wrist": 1})

# bow: about 30 degrees, hips back, hands together in front, head follows, a held beat, up.
s = Seq()
s.k(0)
s.k(0.4, CLASP, torso=(1, 3, -3), head=(-2, -1, -1), cog=(0.012, -0.022, 0.01), cog_rot=(0.5, 2.5, 2))
s.k(1.0, torso=(17, 1.5, -1.5), cog_rot=(12, 1, 1), cog=(0.01, -0.024, -0.035), head=(9, 0, 0),
    hand_L=(0.05, 0.93, 0.33), hand_R=(-0.055, 0.915, 0.328), elbow_L=(0.48, 1.06, 0.1), elbow_R=(-0.48, 1.08, 0.12))
s.k(1.45, torso=(18, 1.5, -1.5), head=(10, 0, 0), hand_L=(0.05, 0.932, 0.335), hand_R=(-0.055, 0.917, 0.333))
s.k(2.0, CLASP, torso=(1.5, 4, -4), cog_rot=(0.5, 4, 3.5), cog=(0.02, -0.022, 0.01), head=(-2, -1, 1))
s.k(2.3, hand_L=(0.156, 0.841, 0.104), hand_R=(-0.146, 0.85, 0.121), wrist_L=(10, -4, -12), wrist_R=(12, -5, -10),
    elbow_L=(0.072, 0.952, -0.303), elbow_R=(-0.06, 0.95, -0.3), fingers_L=(0.4, 0.12, 0.35, 0.45), fingers_R=(0.43, 0.14, 0.32, 0.45),
    head=(-1.5, -1.5, -2))
s.to(2.6, BASE)
clip("bow", 2.6, s, kind="gesture", when="polite bow, thank you, apology", hands_touch=True, lag={"head": 4, "fingers": 4, "wrist": 2})

# point_self: "me?" The right index finger touches her chest, head tilts, a small nod.
SELF = dict(hand_R=(-0.115, 1.131, 0.16), elbow_R=(-0.4, 0.95, 0.1), wrist_R=(84, 4, 72), **point("R", 1.0))
s = Seq()
s.k(0)
s.k(0.22, hand_R=(-0.166, 0.83, 0.061), wrist_R=(12, -5, -5))
s.k(0.4, hand_R=(-0.15, 0.99, 0.15), elbow_R=(-0.3, 0.88, -0.12), wrist_R=(40, 2, 35))  # the elbow swings out, not through
s.k(0.58, SELF, torso=(-3, 6, -8), head=(-1, -4, 7), shoulder_R=(4, 1), hand_L=(0.144, 0.846, 0.081))
s.k(0.86, head=(4, -4, 8))  # "me?" nod
s.k(1.05, head=(0, -3, 7), hand_R=(-0.12, 1.12, 0.19), wrist_R=(72, 4, 70))  # the finger lifts off
s.k(1.3, hand_R=(-0.13, 1.1, 0.21), wrist_R=(58, 3, 60), torso=(-2, 6, -7))
s.k(1.48, hand_R=(-0.15, 0.98, 0.17), elbow_R=(-0.3, 0.88, -0.12), wrist_R=(30, -3, 20), torso=(-0.5, 5.8, -6.5))
s.k(1.68, hand_R=(-0.165, 0.876, 0.103), elbow_R=(-0.06, 0.95, -0.3), wrist_R=(12, -5, -10), fingers_R=(0.42, 0.15, 0.3, 0.42),
    fcurl_R=(-0.05, 0.02, 0.08, 0.16), torso=BASE["torso"], head=(-1.5, -2, -2), shoulder_R=(-2, 1.5))
s.to(1.9, BASE)
clip("point_self", 1.9, s, kind="gesture", when="talking about herself, 'me?', 'I'm Annie'",
     contacts=[("R", 0.6, 0.95, "torso", "index", 0.004)])

# excited_bounce: a real little hop. Squat, spring up (heels, then toes leave the floor), land
# softly, a rebound, fists pumping "yay".
FEET_HOP = dict(foot_L=(0.012, 0.07, -0.006, 22, 5, 0), foot_R=(-0.022, 0.07, 0.049, 22, -16, 0), toes_L=6, toes_R=6)
FISTS = dict(hand_L=(0.13, 1.2, 0.25), hand_R=(-0.13, 1.19, 0.25), elbow_L=(0.241, 0.86, -0.18), elbow_R=(-0.241, 0.86, -0.18),
             wrist_L=(10, 0, 30), wrist_R=(10, 0, 30), **fists("L"), **fists("R"))
s = Seq()
s.k(0)
s.k(0.26, cog=(0.012, -0.075, 0.01), cog_rot=(6, 2, 2), torso=(8, 2, -2), head=(4, 0, 0), hand_L=(0.17, 0.9, 0.17),
    hand_R=(-0.17, 0.9, 0.17), elbow_L=(0.084, 0.932, -0.253), elbow_R=(-0.07, 0.93, -0.35), **fists("L", 0.7), **fists("R", 0.7))
s.k(0.44, FISTS, cog=(0.01, 0.016, 0.006), cog_rot=(0, 1, 1), torso=(-4, 1, -1), head=(-6, 0, 3), **heel("L", 1.0),
    **heel("R", 1.0), shoulder_L=(6, 0), shoulder_R=(6, 0))  # takeoff
s.k(0.58, FEET_HOP, cog=(0.01, 0.042, 0.004), torso=(-5, 1, -1), head=(-8, 0, 4), hand_L=(0.13, 1.25, 0.24),
    hand_R=(-0.13, 1.24, 0.24))  # airborne
s.k(0.69, **heel("L", 0.8), **heel("R", 0.8), cog=(0.012, 0.008, 0.01), cog_rot=(2, 2, 2),
    torso=(1, 2, -2), head=(-3, 0, 3), hand_L=(0.135, 1.2, 0.245), hand_R=(-0.135, 1.19, 0.245), shoulder_L=(4, 0),
    shoulder_R=(4, 0))  # touch down
s.k(0.84, foot_L=BASE["foot_L"], foot_R=BASE["foot_R"], toes_L=0, toes_R=0, cog=(0.012, -0.058, 0.01), cog_rot=(5, 2, 2), torso=(6, 2, -2), head=(3, 0, 2), hand_L=(0.14, 1.12, 0.25),
    hand_R=(-0.14, 1.11, 0.25), shoulder_L=(1, 0), shoulder_R=(1, 0))  # absorb
s.k(1.02, cog=(0.012, 0.0, 0.008), cog_rot=(1, 2, 2), **heel("L", 0.5), **heel("R", 0.5), torso=(-3, 2, -2), head=(-5, 0, -3),
    hand_L=(0.13, 1.21, 0.24), hand_R=(-0.13, 1.2, 0.24), shoulder_L=(5, 0), shoulder_R=(5, 0))  # rebound
s.k(1.2, cog=(0.014, -0.04, 0.01), **heel("L", 0), **heel("R", 0), torso=(4, 3, -3), head=(1, 0, -2),
    hand_L=(0.14, 1.13, 0.24), hand_R=(-0.14, 1.12, 0.24), shoulder_L=(1, 0), shoulder_R=(1, 0))
s.k(1.42, cog=(0.02, -0.02, 0.01), torso=(0, 4, -4), head=(-3, -1, 2), hand_L=(0.13, 1.16, 0.24), hand_R=(-0.13, 1.15, 0.24),
    shoulder_L=(3, 0), shoulder_R=(3, 0))
s.k(1.62, cog=(0.026, -0.024, 0.012), hand_L=(0.2, 0.88, 0.15), hand_R=(-0.2, 0.88, 0.15), **fists("L", 0.1), **fists("R", 0.1),
    wrist_L=(8, -3, -5), wrist_R=(8, -3, -5), torso=(1.5, 5, -5.5), head=(-1.5, -1.5, -1), shoulder_L=(-2, 1),
    shoulder_R=(-2, 1.5), elbow_L=(0.072, 0.952, -0.303), elbow_R=(-0.06, 0.95, -0.3), cog_rot=BASE["cog_rot"])
s.to(1.9, BASE)
clip("excited_bounce", 1.9, s, kind="gesture", when="hyped, can't contain excitement, 'yes!'", feet_move=True,
     lag={"head": 2, "fingers": 3, "wrist": 2})


# ---------------------------------------------------------------------------------------- dance
def dance():
    """Cute 8-count + 4 at 120 bpm, in place with step-touches. Dips land on 0.25 + 0.5 k s."""
    B = [0.25 + 0.5 * k for k in range(12)]
    DF = dict(elbow_L=(0.241, 0.88, -0.15), elbow_R=(-0.241, 0.88, -0.15), wrist_L=(10, 0, 30), wrist_R=(10, 0, 30),
              **fists("L", 0.8), **fists("R", 0.8))
    FL, FR = BASE["foot_L"], BASE["foot_R"]
    s = Seq()
    s.k(0)
    s.k(0.1, **fists("L", 0.45), **fists("R", 0.45), cog=(0.012, -0.03, 0.012), torso=(2, 2, -2.5),
        hand_L=(0.16, 0.9, 0.13), hand_R=(-0.16, 0.9, 0.12), elbow_L=(0.15, 0.92, -0.27), elbow_R=(-0.15, 0.92, -0.27),
        wrist_L=(8, -2, 10), wrist_R=(8, -2, 10))  # fists begin to close as the knees give
    # 1-2: step-touch to her right (right foot steps out, left closes and touches)
    s.k(B[0], DF, cog=(-0.03, -0.05, 0.02), cog_rot=(1, -5, -6), torso=(2, -6, 6), head=(2, 4, -8),
        foot_R=(-0.085, 0, 0.03, 0, -12, 0), hand_L=(0.1, 0.99, 0.21), hand_R=(-0.18, 1.02, 0.19),
        elbow_R=(-0.276, 0.95, -0.12), elbow_L=(0.22, 0.85, -0.25))  # arms rise over the first two counts
    s.k(B[0] + 0.25, cog=(-0.04, -0.022, 0.018), foot_L=(-0.04, 0.03, 0.01, -5, 0, 0), hand_L=(0.1, 1.08, 0.25),
        hand_R=(-0.14, 1.1, 0.25), head=(-1, 2, -4), torso=(3, -3, 3))
    s.k(B[1], cog=(-0.045, -0.05, 0.018), foot_L=(-0.03, 0.03, 0.0, 20, 0, 0), toes_L=20, hand_L=(0.03, 1.13, 0.27),
        hand_R=(-0.25, 1.3, 0.2), torso=(2, -6, 6), head=(2, 5, -9))  # touch
    # 3-4: step-touch back to her left
    s.k(B[1] + 0.25, cog=(-0.01, -0.022, 0.014), foot_L=(0.02, 0.03, 0.0, -3, 5, 0), toes_L=0, hand_L=(0.12, 1.08, 0.25),
        hand_R=(-0.12, 1.08, 0.25), head=(-1, 0, 0), torso=(3, 0, 0))
    s.k(B[2], cog=(0.035, -0.05, 0.012), cog_rot=(1, 6, 6), torso=(2, 7, -7), head=(2, -4, 8), foot_L=(0.03, 0, 0.0, 0, 8, 0),
        hand_L=(0.25, 1.3, 0.2), hand_R=(-0.02, 1.13, 0.27), elbow_L=(0.09, 0.922, -0.288), elbow_R=(-0.22, 0.85, -0.25))
    s.k(B[2] + 0.25, cog=(0.04, -0.022, 0.014), foot_R=(-0.035, 0.03, 0.03, -5, -8, 0), hand_L=(0.14, 1.1, 0.25),
        hand_R=(-0.1, 1.08, 0.25), head=(-1, -2, 4), torso=(3, 3, -3))
    s.k(B[3], cog=(0.042, -0.05, 0.012), foot_R=(-0.02, 0.03, 0.03, 20, -10, 0), toes_R=20, hand_L=(0.25, 1.3, 0.2),
        hand_R=(-0.03, 1.13, 0.27), torso=(2, 7, -7), head=(2, -5, 9))  # touch
    # 5-6: points up, right then left, hip pops
    PU_R = dict(hand_R=(-0.3, 1.46, 0.22), elbow_R=(-0.46, 1.1, -0.18), wrist_R=(0, 0, 0), **point("R"))
    PU_L = mirror(PU_R)
    s.k(B[3] + 0.25, DF, foot_R=(-0.022, 0.02, 0.05, -3, -16, 0), toes_R=0, foot_L=FL, cog=(0.01, -0.022, 0.014),
        hand_L=(0.12, 1.06, 0.24), hand_R=(-0.14, 1.12, 0.25), head=(-1, 0, 0), torso=(3, 0, 0))
    s.k(B[4], PU_R, hand_L=(0.1, 1.06, 0.25), cog=(0.035, -0.05, 0.012), cog_rot=(0, 7, 7), torso=(-2, 6, -10),
        head=(-6, -8, -7), **heel("R", 0.6))
    s.k(B[4] + 0.25, hand_R=(-0.2, 1.24, 0.25), cog=(0.012, -0.024, 0.012), head=(-2, -3, -2), **heel("R", 0))
    s.k(B[5], DF, PU_L, hand_R=(-0.1, 1.06, 0.25),
        cog=(-0.02, -0.05, 0.02), cog_rot=(0, -6, -6), torso=(-2, -6, 10), head=(-6, 8, 7), **heel("L", 0.6))
    s.k(B[5] + 0.25, hand_L=(0.2, 1.24, 0.25), cog=(0.01, -0.024, 0.014), head=(-2, 3, 2), **heel("L", 0))
    # 7-8: arms roll in front, clap on 8
    s.k(B[6], DF, hand_L=(0.09, 1.1, 0.3), hand_R=(-0.08, 1.02, 0.3), cog=(0.02, -0.05, 0.014), cog_rot=(1, 3, 3),
        torso=(4, 3, -3), head=(3, -2, -3))
    s.k(B[6] + 0.125, hand_L=(0.09, 1.02, 0.3), hand_R=(-0.08, 1.1, 0.3))
    s.k(B[6] + 0.25, hand_L=(0.09, 1.1, 0.3), hand_R=(-0.08, 1.02, 0.3), cog=(0.02, -0.024, 0.014), head=(-1, 2, 3))
    s.k(B[6] + 0.375, hand_L=(0.1, 1.16, 0.3), hand_R=(-0.06, 1.17, 0.3), wrist_L=(-20, 0, 0), wrist_R=(-20, 0, 0),
        fingers_L=(0.1, 0.2, 0.1, 0.2), fingers_R=(0.1, 0.2, 0.1, 0.2), fcurl_L=ZERO4, fcurl_R=ZERO4)
    s.k(B[7], hand_L=(0.048, 1.175, 0.31), hand_R=(-0.004, 1.18, 0.31), cog=(0.02, -0.05, 0.014), torso=(2, 3, -3),
        head=(0, 1, 6))  # clap
    # 9-10: shoulder shimmy with little fists
    s.k(B[7] + 0.25, DF, hand_L=(0.14, 1.12, 0.26), hand_R=(-0.14, 1.12, 0.26), cog=(0.02, -0.024, 0.014), head=(-2, 0, 3))
    for j, t in enumerate((B[8], B[8] + 0.25, B[9], B[9] + 0.25)):
        d = 1 if j % 2 == 0 else -1
        s.k(t, shoulder_L=(9 if d > 0 else -2, 3 if d > 0 else -2), shoulder_R=(-2 if d > 0 else 9, -2 if d > 0 else 3),
            torso=(2, 2 * d, -8 * d), cog=(0.02 + 0.012 * d, -0.05 if j % 2 == 0 else -0.03, 0.014), cog_rot=(1, 3 * d, 4 * d),
            head=(1, -4 * d, 6 * d), hand_L=(0.14, 1.12 + 0.02 * d, 0.27), hand_R=(-0.14, 1.12 - 0.02 * d, 0.27))
    # 11-12: fist to cheek, a wink-pose on the last beat, settle
    s.k(B[10], fists("R", 0.7), fists("L", 0.3), shoulder_L=(2, 0), shoulder_R=(2, 0), hand_R=(-0.12, 1.3, 0.21),
        wrist_R=(12, 0, 20), hand_L=(0.16, 1.0, 0.22), wrist_L=(5, 0, 10), elbow_L=(0.052, 0.942, -0.333), elbow_R=(-0.248, 0.88, 0.0),
        head=(-3, -6, -10), torso=(0, 5, -9), cog=(0.03, -0.05, 0.012), cog_rot=(1, 5, 5), **heel("L", 0.5))
    s.k(B[10] + 0.25, cog=(0.03, -0.026, 0.012), head=(-3, -6, -11))
    s.k(B[11], cog=(0.03, -0.045, 0.012), head=(-3, -7, -12), torso=(1, 5, -9), **heel("L", 0.4))
    s.to(6.25, BASE)
    return s


clip("dance", 6.25, dance(), kind="action", when="dancing, celebrating to music, party", feet_move=True,
     lag={"head": 2, "fingers": 2, "wrist": 1})

# ---------------------------------------------------------------------------------------- talk rests
# Calm holds in the talk-rest postures: breathing-scale motion only (the gesture phrases carry the
# gesture). low for calm speech, mid for animated / excited.


def talk_rest(name, rest, dur, energy, amp=1.0, seed=0):
    base = {"low": REST_LOW, "mid": REST_MID}[rest]
    s = Seq(base)
    s.k(0)
    k = [(0.3, 0.2), (0.55, -0.35), (0.8, 0.25)]
    for i, (f, ph) in enumerate(k):
        t = dur * f
        sg = 1 if (i + seed) % 2 == 0 else -1
        s.d(t, hand_L=(0.003 * amp * sg, 0.004 * amp * (ph + 0.5), 0.003 * amp), hand_R=(-0.003 * amp * sg, 0.004 * amp * (0.5 - ph), 0.002 * amp),
            wrist_L=(2 * amp * sg, 0, 3 * amp), wrist_R=(-2 * amp * sg, 0, -3 * amp), head=(0.8 * amp * sg, 1.2 * amp * ph, 0.8 * amp),
            torso=(0.4 * amp * sg, 0.4 * amp, 0.6 * amp * ph))
        # the next key's offsets are around the rest again, not cumulative
        s.cur = {**s.cur, **{c: base[c] for c in ("hand_L", "hand_R", "wrist_L", "wrist_R", "head", "torso")}}
    s.to(dur, base)
    clip(name, dur, s, loop=True, layer="talk", energy=energy, rest=rest, when=f"{energy} talk rest while Annie speaks",
         lag={"head": 6, "fingers": 8, "wrist": 5, "arms": 3})


talk_rest("talk_calm_1", "low", 5.0, "calm", 1.0, 0)
talk_rest("talk_calm_2", "low", 5.5, "calm", 0.8, 1)
talk_rest("talk_calm_3", "low", 6.0, "calm", 1.2, 0)
talk_rest("talk_animated_1", "mid", 5.0, "animated", 1.2, 1)
talk_rest("talk_animated_2", "mid", 4.5, "animated", 1.0, 0)
talk_rest("talk_animated_3", "mid", 5.5, "animated", 1.4, 1)
talk_rest("talk_excited_1", "mid", 4.0, "excited", 1.8, 0)
talk_rest("talk_excited_2", "mid", 4.5, "excited", 2.0, 1)

# ---------------------------------------------------------------------------------------- phrases
import phrases  # noqa: E402

CLIPS.extend(phrases.PHRASES)
POSES = {"CLASP": P(**CLASP), "HOLD": P(**HOLD), "THINK": P(**THINK), "MOUTH": P(**MOUTH), "SAD": P(**SAD), "RECOIL": P(**RECOIL),
         "SELF": P(**SELF), "SHRUG": P(**SHRUG), "EAR_R": P(**EAR_R), "UP": P(**UP), "FISTS": P(**FISTS), "HIPS": P(**HIPS),
         "CLAP_HIT": P(**CLAP_HIT), **phrases.POSES}
