"""Keyframe Annie's control rig from clip data (motion/blender/clips.py).

A clip is a list of key poses. Each pose sets any subset of these channels (character space:
x = her left, y = up, z = forward, metres and degrees):

  cog       (x, y, z)            COG offset from rest (the hips; feet stay planted)
  cog_rot   (pitch, yaw, roll)   + pitch = tip forward, + yaw = turn to her left, + roll = left hip up
  torso     (fwd, side, twist)   spine bend spread over spine/chest/upperChest (+ side = lean to her left)
  head      (pitch, yaw, tilt)   neck 35% / head 65% (+ pitch = chin down, + tilt = ear to her left shoulder)
  hand_L/R  (x, y, z)            wrist position, absolute, in the upper chest's frame at rest
  elbow_L/R (x, y, z)            elbow pole position (same frame)
  wrist_L/R (flex, dev, twist)   + flex = toward the palm, + dev = toward the thumb, + twist = supinate
  fingers_L/R (curl, spread, thumb_curl, thumb_in)   and fcurl_L/R (index, middle, ring, little) offsets
  shoulder_L/R (raise, fwd)      shoulder shrug / reach, deg
  foot_L/R  (x, y, z, pitch, yaw, roll)  offset from the rest ankle; rotation of the foot
  toes_L/R  pitch                + = toes bend up (heel raised)

Keys are Bezier (auto-clamped) unless a key says otherwise: key options `ease` = "linear" | "const" |
"<blender easing>" (e.g. "BACK", "EXPO") applied from that key to the next. `lag` on a clip delays
channel groups by n frames (overlapping action: head and fingers trail the arms). Loops get a Cycles
modifier so the Bezier handles wrap and frame N-1 flows into frame 0.
"""

import math

import bpy
from mathutils import Euler, Vector

import rig

D2R = math.pi / 180


def _set(arm, path, value, frame, index=-1):
    arm.keyframe_insert(data_path=path, index=index, frame=frame)


class Keyer:
    def __init__(self, arm):
        self.arm = arm
        P = arm.pose.bones
        V = rig.VRM
        self.rest = {}
        for s, S in rig.SIDES.items():
            self.rest[f"hand_{S}"] = rig.b2c(P[f"CTRL_hand_{S}"].bone.head_local)
            self.rest[f"elbow_{S}"] = rig.b2c(P[f"CTRL_elbow_{S}"].bone.head_local)
        self.rest["hips"] = rig.b2c(P[V["hips"]].bone.head_local)

    def clear(self):
        arm = self.arm
        if arm.animation_data and arm.animation_data.action:
            a = arm.animation_data.action
            arm.animation_data.action = None
            bpy.data.actions.remove(a)
        for pb in arm.pose.bones:
            pb.location = (0, 0, 0)
            pb.rotation_euler = (0, 0, 0)
        for s in ("L", "R"):
            h = arm.pose.bones[f"CTRL_hand_{s}"]
            for k in list(h.keys()):
                h[k] = 0.0
        for n in ("CTRL_torso", "CTRL_head"):
            for k in list(arm.pose.bones[n].keys()):
                arm.pose.bones[n][k] = 0.0

    def key_pose(self, pose, frame):
        """Set + key every channel in `pose` at `frame`. Returns the data paths keyed (for easing)."""
        arm, P = self.arm, self.arm.pose.bones
        V = rig.VRM
        keyed = []

        def loc(bone, v):
            P[bone].location = v
            arm.keyframe_insert(f'pose.bones["{bone}"].location', frame=frame)
            keyed.append(f'pose.bones["{bone}"].location')

        def rot(bone, e):
            P[bone].rotation_euler = e
            arm.keyframe_insert(f'pose.bones["{bone}"].rotation_euler', frame=frame)
            keyed.append(f'pose.bones["{bone}"].rotation_euler')

        def prop(bone, k, v):
            P[bone][k] = float(v)
            p = f'pose.bones["{bone}"]["{k}"]'
            arm.keyframe_insert(p, frame=frame)
            keyed.append(p)

        for ch, v in pose.items():
            if ch == "cog":
                loc("CTRL_cog", Vector(v))
            elif ch == "cog_rot":
                rot("CTRL_cog", Euler([x * D2R for x in v], "XYZ"))
            elif ch == "torso":
                for k, x in zip(("bend_fwd", "bend_side", "twist"), v):
                    prop("CTRL_torso", k, x)
            elif ch == "head":
                for k, x in zip(("pitch", "yaw", "tilt"), v):
                    prop("CTRL_head", k, x)
            elif ch == "aim":
                prop("CTRL_head", "aim", v)
            elif ch == "look":
                loc("CTRL_look", Vector(v))
            elif ch.startswith(("hand_", "elbow_")):
                loc(f"CTRL_{ch}", Vector(v) - self.rest[ch])
            elif ch.startswith("wrist_"):
                for k, x in zip(("flex", "dev", "twist"), v):
                    prop(f"CTRL_hand_{ch[-1]}", k, x)
            elif ch.startswith("fingers_"):
                for k, x in zip(("curl", "spread", "thumb_curl", "thumb_in"), v):
                    prop(f"CTRL_hand_{ch[-1]}", k, x)
            elif ch.startswith("fcurl_"):
                for k, x in zip(("curl_index", "curl_middle", "curl_ring", "curl_little"), v):
                    prop(f"CTRL_hand_{ch[-1]}", k, x)
            elif ch.startswith("shoulder_"):
                s = "left" if ch[-1] == "L" else "right"
                sg = 1 if s == "left" else -1
                raise_, fwd = v
                # shoulder bone local axes vary; rotate about character axes: raise = about forward axis
                pb = P[V[f"{s}Shoulder"]]
                R = pb.bone.matrix_local.to_3x3().transposed()
                a_raise = R @ rig.c2b((0, 0, 1))  # + about forward lifts the left shoulder
                a_fwd = R @ rig.c2b((0, 1, 0))  # about up: + swings the left shoulder forward? (sign below)
                e = (a_raise * (sg * raise_ * D2R)) + (a_fwd * (-sg * fwd * D2R))
                rot(V[f"{s}Shoulder"], Euler(e, "XYZ"))
            elif ch.startswith("foot_"):
                S = ch[-1]
                x, y, z, pitch, yaw, roll = (list(v) + [0, 0, 0])[:6]
                loc(f"CTRL_foot_{S}", Vector((x, y, z)))
                rot(f"CTRL_foot_{S}", Euler((pitch * D2R, yaw * D2R, roll * D2R), "XYZ"))
            elif ch.startswith("toes_"):
                s = "left" if ch[-1] == "L" else "right"
                pb = P[V[f"{s}Toes"]]
                R = pb.bone.matrix_local.to_3x3().transposed()
                rot(V[f"{s}Toes"], Euler(R @ rig.c2b((-v * D2R, 0, 0)), "XYZ"))
            else:
                raise KeyError(f"unknown channel {ch}")
        return keyed


def group_of(ch):
    if ch in ("head", "look", "aim"):
        return "head"
    if ch.startswith(("fingers_", "fcurl_")):
        return "fingers"
    if ch.startswith("wrist_"):
        return "wrist"
    if ch.startswith(("hand_", "elbow_")):
        return "arms"
    if ch.startswith("shoulder_"):
        return "shoulders"
    if ch in ("torso",):
        return "torso"
    return "body"


def fcurves(arm):
    act = arm.animation_data.action
    try:
        from bpy_extras.anim_utils import action_get_channelbag_for_slot
        cb = action_get_channelbag_for_slot(act, arm.animation_data.action_slot)
        return list(cb.fcurves)
    except ImportError:  # pre-slotted actions
        return list(act.fcurves)


def apply_clip(keyer, clip, fps=30):
    """Key the clip's poses on the rig. Returns the number of frames to bake."""
    arm = keyer.arm
    keyer.clear()
    arm.animation_data_create()
    act = bpy.data.actions.new(clip["name"])
    arm.animation_data.action = act
    loop = clip.get("loop", False)
    n = int(round(clip["duration"] * fps)) + (0 if loop else 1)
    period = n if loop else None
    lag = clip.get("lag", {})
    eases = []  # (frame, paths, ease)
    keys = clip["keys"]
    last = len(keys) - 1
    for i, k in enumerate(keys):
        t, pose = k[0], k[1]
        opts = k[2] if len(k) > 2 else {}
        base_f = t * fps
        by_lag = {}
        for ch, v in pose.items():
            L = lag.get(group_of(ch), 0) if (loop or 0 < i < last) else 0
            if isinstance(L, dict):
                L = L.get(ch, 0)
            by_lag.setdefault(L, {})[ch] = v
        for L, sub in by_lag.items():
            f = base_f + L
            if not loop:
                f = min(f, n - 1)
            paths = keyer.key_pose(sub, f)
            if "ease" in opts:
                eases.append((f, set(paths), opts["ease"]))
    fcs = fcurves(arm)
    for fc in fcs:
        for kp in fc.keyframe_points:
            kp.interpolation = "BEZIER"
            kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
    for f, paths, ease in eases:
        for fc in fcs:
            if fc.data_path not in paths:
                continue
            for kp in fc.keyframe_points:
                if abs(kp.co.x - f) < 1e-3:
                    if ease == "linear":
                        kp.interpolation = "LINEAR"
                    elif ease == "const":
                        kp.interpolation = "CONSTANT"
                    elif ease == "vector":
                        kp.handle_left_type = kp.handle_right_type = "VECTOR"
                    else:
                        name, _, mode = ease.partition(":")
                        kp.interpolation = name
                        kp.easing = mode or "AUTO"
    if loop:
        for fc in fcs:
            fc.modifiers.new("CYCLES")
    for fc in fcs:
        fc.update()
    smooth(fcs, n, loop, clip.get("smooth", 1))
    return n, period


def smooth(fcs, n, loop, passes=1):
    """C2 cushioning: resample every control curve per frame and run a [1, 2, 1] / 4 filter.

    Bezier keys are C1: at every key the acceleration jumps (worst where a fast stroke lands in a
    hold), which reads as a pop, the runtime's "velocity step". One pass spreads each jump over three
    frames (gain 0.93 at 2.5 Hz, 0.75 at 5 Hz) and leaves holds and contacts exactly where they are.
    Loops wrap; one-shots keep their first and last frame (the rest pose they blend from / to)."""
    if passes <= 0:
        return
    import numpy as np
    LINEAR = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["LINEAR"].value
    m = n + (1 if loop else 0)  # a loop needs its end key (== frame 0) for the CYCLES period
    for fc in fcs:
        v = np.array([fc.evaluate(f) for f in range(n)])
        if np.ptp(v) < 1e-9:
            continue
        for _ in range(passes):
            if loop:
                v = (np.roll(v, 1) + 2 * v + np.roll(v, -1)) / 4
            else:
                v[1:-1] = (v[:-2] + 2 * v[1:-1] + v[2:]) / 4
        for mod in list(fc.modifiers):
            fc.modifiers.remove(mod)
        fc.keyframe_points.clear()
        fc.keyframe_points.add(m)
        co = np.empty(2 * m)
        co[0::2] = np.arange(m)
        co[1::2] = v[np.arange(m) % n]
        fc.keyframe_points.foreach_set("co", co)
        fc.keyframe_points.foreach_set("interpolation", [LINEAR] * m)
        if loop:
            fc.modifiers.new("CYCLES")
        fc.update()
