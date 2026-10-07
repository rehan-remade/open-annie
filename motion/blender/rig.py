"""Annie's control rig, built headless over the VRM humanoid armature (Blender 5.2 + VRM Add-on).

Import: the VRM Add-on for Blender (saturday06, MIT) imports assets/avatars/annie-b/model.vrm as a
Z-up armature facing -Y with the character's left at +X (the VRoid J_Bip_* bones, T-pose rest).

Control rig (non-deform bones added to the same armature; the deform bones are driven by constraints
and drivers, so the animator only touches the CTRL_* bones and their custom properties):

  CTRL_cog              COG / torso root. Parent of the hips: moves and rotates the whole body
                        over the planted feet. Axes: x = her left, y = up, z = forward.
  CTRL_torso            props bend_fwd / bend_side / twist (deg), spread over spine/chest/upperChest
                        (0.30 / 0.35 / 0.35) by drivers: a bend control over the FK spine chain.
  CTRL_head             props pitch / yaw / tilt (deg), spread neck 35% / head 65%. Optional aim:
                        prop `aim` (0..1) blends a Damped Track of the head toward CTRL_look.
  CTRL_look             look-at target in front of the face (child of the upper chest).
  CTRL_hand_L/R         arm IK target at the wrist (child of the upper chest, so hands ride the torso).
                        props: flex / dev / twist (wrist, deg; twist = supination, split 50/50 with the
                        forearm), curl / spread / thumb_curl / thumb_in (0..1-ish), and per-finger
                        curl offsets curl_index / curl_middle / curl_ring / curl_little.
  CTRL_elbow_L/R        elbow pole targets (child of the upper chest).
  FIX_hand_L/R, FIX_elbow_L/R   parents of the hand / elbow controls, keyed per frame by the
                        collision solver (export.py + collide.py), never by hand.
  CTRL_foot_L/R         leg IK targets at the ankles (child of Root: planted). Rotating one rolls the
                        foot (the deform foot copies its world rotation). CTRL_knee_L/R: knee poles,
                        children of the feet.
  CTRL_shoulder_L/R     (the deform J_Bip_*_Shoulder bones are keyed directly, FK shrug/forward).

Arms: IK solves a hidden MCH_upperArm/MCH_lowerArm chain (elbow = hinge about the forearm's local Z,
with flexion limits); the deform arm copies it and adds the forearm twist on top. Legs: IK directly on
the deform thigh/shin (knee = hinge about local X, no hyperextension). Feet planted by construction.
"""

import math
from pathlib import Path

import addon_utils
import bpy
from mathutils import Matrix, Vector

from poses import avatar_hide, avatar_path  # noqa: E402  (plain-Python module: the avatar the pack is built for)

AVATAR = None  # resolved in import_avatar(); see poses.AVATAR_CANDIDATES
HIDE = {}  # the outfit parts removed at load (assets/avatars/index.json `hide`), as the runtime does
D2R = math.pi / 180

# VRM humanoid bone (VRM 1.0 names) -> VRoid bone. Thumb: VRM 0.x Proximal/Intermediate/Distal
# are VRM 1.0 Metacarpal/Proximal/Distal.
SIDES = {"left": "L", "right": "R"}


def vrm_bone_map():
    m = {"hips": "J_Bip_C_Hips", "spine": "J_Bip_C_Spine", "chest": "J_Bip_C_Chest", "upperChest": "J_Bip_C_UpperChest",
         "neck": "J_Bip_C_Neck", "head": "J_Bip_C_Head"}
    for s, S in SIDES.items():
        m.update({f"{s}Shoulder": f"J_Bip_{S}_Shoulder", f"{s}UpperArm": f"J_Bip_{S}_UpperArm", f"{s}LowerArm": f"J_Bip_{S}_LowerArm",
                  f"{s}Hand": f"J_Bip_{S}_Hand", f"{s}UpperLeg": f"J_Bip_{S}_UpperLeg", f"{s}LowerLeg": f"J_Bip_{S}_LowerLeg",
                  f"{s}Foot": f"J_Bip_{S}_Foot", f"{s}Toes": f"J_Bip_{S}_ToeBase"})
        for i, p in enumerate(("Metacarpal", "Proximal", "Distal")):
            m[f"{s}Thumb{p}"] = f"J_Bip_{S}_Thumb{i + 1}"
        for f in ("Index", "Middle", "Ring", "Little"):
            for i, p in enumerate(("Proximal", "Intermediate", "Distal")):
                m[f"{s}{f}{p}"] = f"J_Bip_{S}_{f}{i + 1}"
    return m


VRM = vrm_bone_map()
# VRM humanoid parent (nearest humanoid ancestor), the hierarchy three-vrm's normalized rig uses.
VRM_PARENT = {"hips": None, "spine": "hips", "chest": "spine", "upperChest": "chest", "neck": "upperChest", "head": "neck"}
for _s in SIDES:
    VRM_PARENT.update({f"{_s}Shoulder": "upperChest", f"{_s}UpperArm": f"{_s}Shoulder", f"{_s}LowerArm": f"{_s}UpperArm",
                       f"{_s}Hand": f"{_s}LowerArm", f"{_s}UpperLeg": "hips", f"{_s}LowerLeg": f"{_s}UpperLeg",
                       f"{_s}Foot": f"{_s}LowerLeg", f"{_s}Toes": f"{_s}Foot",
                       f"{_s}ThumbMetacarpal": f"{_s}Hand", f"{_s}ThumbProximal": f"{_s}ThumbMetacarpal",
                       f"{_s}ThumbDistal": f"{_s}ThumbProximal"})
    for _f in ("Index", "Middle", "Ring", "Little"):
        VRM_PARENT.update({f"{_s}{_f}Proximal": f"{_s}Hand", f"{_s}{_f}Intermediate": f"{_s}{_f}Proximal",
                           f"{_s}{_f}Distal": f"{_s}{_f}Intermediate"})

# Character space (x = her left, y = up, z = forward; = VRM 1.0 space) <-> Blender world (Z up, facing -Y).
C2B = Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))
B2C = C2B.transposed()


def c2b(v):
    return C2B @ Vector(v)


def b2c(v):
    return B2C @ Vector(v)


FINGERS = ("index", "middle", "ring", "little")
# finger-joint curl at curl = 1 (deg): proximal, intermediate, distal
CURL_DEG = (62, 88, 58)
SPREAD_DEG = {"index": 9, "middle": 1, "ring": -7, "little": -15}  # + = toward the thumb


def import_avatar(root):
    global AVATAR
    AVATAR = avatar_path(root)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("bl_ext.user_default.vrm", default_set=True)
    bpy.ops.import_scene.vrm(filepath=str(Path(root) / AVATAR))
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    assert arm.matrix_world == Matrix.Identity(4), "armature object must be untransformed"
    global HIDE
    HIDE = avatar_hide(root, AVATAR)
    remove_outfit_parts(HIDE)
    return arm


def remove_outfit_parts(hide):
    """Delete the faces whose material name contains one of hide["materials"] (the runtime removes the
    same meshes in Character.load), so posing and the collision QA see the outfit that is shown."""
    import bmesh
    subs = hide.get("materials") or []
    if not subs:
        return
    for o in [o for o in bpy.data.objects if o.type == "MESH"]:
        doomed = [i for i, sl in enumerate(o.material_slots) if sl.material and any(k in sl.material.name for k in subs)]
        if not doomed:
            continue
        bm = bmesh.new()
        bm.from_mesh(o.data)
        faces = [f for f in bm.faces if f.material_index in doomed]
        bmesh.ops.delete(bm, geom=faces, context="FACES")
        loose = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(o.data)
        bm.free()
        o.data.update()


def _new(eb, name, head, tail, parent=None, roll=0.0):
    b = eb.new(name)
    b.head, b.tail, b.roll = Vector(head), Vector(tail), roll
    b.use_deform = False
    if parent:
        b.parent = eb[parent]
    return b


def _up(eb, name, at, parent, length=0.08):
    """Control bone at `at` pointing world +Z (local axes: x = left, y = up, z = forward)."""
    return _new(eb, name, at, Vector(at) + Vector((0, 0, length)), parent)


def _driver(pb, prop_path, index, expr, variables):
    """variables: list of (name, id_object, data_path)."""
    fc = pb.id_data.driver_add(f'pose.bones["{pb.name}"].{prop_path}', index)
    d = fc.driver
    d.type = "SCRIPTED"
    for n, ob, path in variables:
        v = d.variables.new()
        v.name = n
        v.type = "SINGLE_PROP"
        v.targets[0].id = ob
        v.targets[0].data_path = path
    d.expression = expr
    return fc


def _prop(pb, name, value=0.0, lo=-360.0, hi=360.0):
    pb[name] = float(value)
    ui = pb.id_properties_ui(name)
    ui.update(min=lo, max=hi, soft_min=lo, soft_max=hi)


def _axis_terms(pb, world_axis):
    """Local euler contributions (x, y, z weights) for a rotation about `world_axis` (unit, rest)."""
    R = pb.bone.matrix_local.to_3x3()
    a = (R.transposed() @ Vector(world_axis)).normalized()
    return a


def build(root):
    arm = import_avatar(root)
    A = arm.data
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = A.edit_bones
    J = lambda n: eb[VRM[n]]  # noqa: E731
    rest = {n: (J(n).head.copy(), J(n).tail.copy()) for n in VRM}

    # COG: parent of the hips.
    _up(eb, "CTRL_cog", rest["hips"][0], "Root", 0.15)
    J("hips").use_connect = False  # before re-parenting, or the hips head snaps to the COG's tail
    J("hips").parent = eb["CTRL_cog"]
    assert (J("hips").head - rest["hips"][0]).length < 1e-6
    _up(eb, "CTRL_torso", rest["upperChest"][0] + Vector((0, 0.25, 0)), "Root", 0.06)
    _up(eb, "CTRL_head", rest["head"][0] + Vector((0, 0.25, 0.1)), "Root", 0.06)
    _up(eb, "CTRL_look", rest["head"][0] + Vector((0, -1.0, 0.05)), VRM["upperChest"], 0.05)
    for s, S in SIDES.items():
        sg = 1 if s == "left" else -1
        # arm IK: MCH chain duplicates the deform arm
        for n in ("UpperArm", "LowerArm"):
            h, t = rest[f"{s}{n}"]
            b = _new(eb, f"MCH_{s}{n}", h, t, VRM[f"{s}Shoulder"] if n == "UpperArm" else f"MCH_{s}UpperArm", J(f"{s}{n}").roll)
            b.use_connect = n == "LowerArm"
        # FIX_* sit between the upper chest and the animator's controls: export.py keys them densely
        # with the collision solver's corrections, so the authored keys stay untouched.
        _up(eb, f"FIX_hand_{S}", rest[f"{s}Hand"][0], VRM["upperChest"], 0.05)
        _up(eb, f"CTRL_hand_{S}", rest[f"{s}Hand"][0], f"FIX_hand_{S}", 0.06)
        _up(eb, f"FIX_elbow_{S}", rest[f"{s}LowerArm"][0] + Vector((0, 0.35, 0)), VRM["upperChest"], 0.03)
        _up(eb, f"CTRL_elbow_{S}", rest[f"{s}LowerArm"][0] + Vector((0, 0.35, 0)), f"FIX_elbow_{S}", 0.04)
        # leg IK
        _up(eb, f"CTRL_foot_{S}", rest[f"{s}Foot"][0], "Root", 0.06)
        fh, ft = rest[f"{s}Foot"]
        _new(eb, f"MCH_foot_{S}", fh, ft, f"CTRL_foot_{S}", J(f"{s}Foot").roll)
        _up(eb, f"CTRL_knee_{S}", rest[f"{s}LowerLeg"][0] + Vector((0.0 * sg, -0.45, 0)), f"CTRL_foot_{S}", 0.04)
    bpy.ops.object.mode_set(mode="POSE")
    P = arm.pose.bones
    for pb in P:
        pb.rotation_mode = "XYZ"

    # torso bend: spread over the FK spine
    tor = P["CTRL_torso"]
    for k in ("bend_fwd", "bend_side", "twist"):
        _prop(tor, k)
    path = lambda b, k: f'pose.bones["{b}"]["{k}"]'  # noqa: E731
    for n, w in (("spine", 0.30), ("chest", 0.35), ("upperChest", 0.35)):
        pb = P[VRM[n]]
        # spine bones: local x = her left, y = up the spine, z = forward (roll 0 VRoid spine)
        terms = {"bend_fwd": _axis_terms(pb, c2b((1, 0, 0))), "twist": _axis_terms(pb, c2b((0, 1, 0))),
                 "bend_side": _axis_terms(pb, c2b((0, 0, -1)))}  # + bend_side = lean to her left
        _drive_sum(arm, pb, terms, "CTRL_torso", w, path)
    hd = P["CTRL_head"]
    for k in ("pitch", "yaw", "tilt"):
        _prop(hd, k)
    _prop(hd, "aim", 0.0, 0.0, 1.0)
    for n, w in (("neck", 0.35), ("head", 0.65)):
        pb = P[VRM[n]]
        terms = {"pitch": _axis_terms(pb, c2b((1, 0, 0))), "yaw": _axis_terms(pb, c2b((0, 1, 0))),
                 "tilt": _axis_terms(pb, c2b((0, 0, -1)))}  # + pitch = nod down, + yaw = turn to her left, + tilt = ear to her left shoulder
        _drive_sum(arm, pb, terms, "CTRL_head", w, path)
    c = P[VRM["head"]].constraints.new("DAMPED_TRACK")
    c.target, c.subtarget, c.track_axis = arm, "CTRL_look", "TRACK_Z"  # head local z = forward
    fc = c.driver_add("influence")
    v = fc.driver.variables.new()
    v.name, v.targets[0].id, v.targets[0].data_path = "a", arm, path("CTRL_head", "aim")
    fc.driver.expression = "a"

    for s, S in SIDES.items():
        sg = 1 if s == "left" else -1
        up_, lo_ = P[f"MCH_{s}UpperArm"], P[f"MCH_{s}LowerArm"]
        ik = lo_.constraints.new("IK")
        ik.target, ik.subtarget = arm, f"CTRL_hand_{S}"
        ik.pole_target, ik.pole_subtarget = arm, f"CTRL_elbow_{S}"
        ik.chain_count = 2
        ik.pole_angle = POLE_ANGLE_ARM[s]
        lo_.lock_ik_x = lo_.lock_ik_y = True
        # elbow = hinge about the forearm's local z (flexion: left negative, right positive). No IK
        # limit: Blender's limit rejects the right arm's solutions; the calibrated pole angle keeps
        # the bend on the flexion side and export.py checks the sign every frame.
        for n, mch in (("UpperArm", up_), ("LowerArm", lo_)):
            d = P[VRM[f"{s}{n}"]]
            ct = d.constraints.new("COPY_TRANSFORMS")
            ct.target, ct.subtarget = arm, mch.name
            ct.target_space = ct.owner_space = "LOCAL"
            ct.mix_mode = "BEFORE_FULL"  # the deform bone's own rotation (forearm twist) is applied after
        hand = P[f"CTRL_hand_{S}"]
        for k, v0 in (("flex", 0), ("dev", 0), ("twist", 0), ("curl", 0.3), ("spread", 0.2), ("thumb_curl", 0.3),
                      ("thumb_in", 0.3), ("curl_index", 0), ("curl_middle", 0), ("curl_ring", 0), ("curl_little", 0)):
            _prop(hand, k, v0)
        hn = f"CTRL_hand_{S}"
        # wrist: palm normal at rest is world -Z (T-pose, palms down), fingers point along +-X, thumb forward
        dh = P[VRM[f"{s}Hand"]]
        fdir = (dh.bone.tail_local - dh.bone.head_local).normalized()
        palm = Vector((0, 0, -1))
        thumb = c2b((0, 0, 1))
        flex_ax = fdir.cross(palm)  # rotates the fingers toward the palm
        dev_ax = fdir.cross(thumb)  # rotates the fingers toward the thumb (radial deviation)
        sup_ax = -fdir if s == "left" else fdir  # supination: the palm turns up / forward, thumb out
        _drive_sum(arm, dh, {"flex": _axis_terms(dh, flex_ax), "dev": _axis_terms(dh, dev_ax),
                             "twist": _axis_terms(dh, sup_ax) * 0.5}, hn, 1.0, path)
        fa = P[VRM[f"{s}LowerArm"]]
        _drive_sum(arm, fa, {"twist": _axis_terms(fa, sup_ax) * 0.5}, hn, 1.0, path)
        # fingers
        for f in FINGERS:
            for i in range(3):
                pb = P[VRM[f"{s}{f.capitalize()}{('Proximal', 'Intermediate', 'Distal')[i]}"]]
                d = (pb.bone.tail_local - pb.bone.head_local).normalized()
                curl_ax = d.cross(palm).normalized()
                a = _axis_terms(pb, curl_ax) * (CURL_DEG[i] * D2R)
                vars_ = [("c", arm, path(hn, "curl")), ("o", arm, path(hn, f"curl_{f}"))]
                exprs = {}
                for j in range(3):
                    exprs[j] = f"({a[j]:.5f})*(c+o)"
                if i == 0:
                    sp = _axis_terms(pb, d.cross(thumb).normalized()) * (SPREAD_DEG[f] * D2R)
                    vars_.append(("s", arm, path(hn, "spread")))
                    for j in range(3):
                        exprs[j] += f"+({sp[j]:.5f})*s"
                for j in range(3):
                    _driver(pb, "rotation_euler", j, exprs[j], vars_)
        # thumb: curl = flex all three joints toward the palm; thumb_in = metacarpal swings under the palm
        for i, p in enumerate(("Metacarpal", "Proximal", "Distal")):
            pb = P[VRM[f"{s}Thumb{p}"]]
            d = (pb.bone.tail_local - pb.bone.head_local).normalized()
            curl_ax = d.cross(palm).normalized()
            k = (15, 40, 50)[i] * D2R
            a = _axis_terms(pb, curl_ax) * k
            vars_ = [("c", arm, path(hn, "thumb_curl"))]
            exprs = {j: f"({a[j]:.5f})*c" for j in range(3)}
            if i == 0:
                # swing toward the little finger (about the palm normal) and down under the palm
                across = _axis_terms(pb, -palm if s == "left" else palm) * (38 * D2R)
                down = _axis_terms(pb, curl_ax) * (20 * D2R)
                vars_.append(("t", arm, path(hn, "thumb_in")))
                for j in range(3):
                    exprs[j] += f"+({across[j] + down[j]:.5f})*t"
            for j in range(3):
                _driver(pb, "rotation_euler", j, exprs[j], vars_)

        # legs
        shin = P[VRM[f"{s}LowerLeg"]]
        ik = shin.constraints.new("IK")
        ik.target, ik.subtarget = arm, f"CTRL_foot_{S}"
        ik.pole_target, ik.pole_subtarget = arm, f"CTRL_knee_{S}"
        ik.chain_count = 2
        ik.pole_angle = POLE_ANGLE_LEG[s]
        shin.lock_ik_y = shin.lock_ik_z = True
        shin.use_ik_limit_x = True
        shin.ik_min_x, shin.ik_max_x = 0.0, 150 * D2R
        ft = P[VRM[f"{s}Foot"]]
        cr = ft.constraints.new("COPY_ROTATION")
        cr.target, cr.subtarget = arm, f"MCH_foot_{S}"
    bpy.context.view_layer.update()
    return arm


def _drive_sum(arm, pb, terms, ctrl, weight, path):
    """pb.rotation_euler[j] = weight * sum_k terms[k][j] * ctrl[k] * deg2rad."""
    names = list(terms)
    for j in range(3):
        parts = [f"({weight * terms[k][j] * D2R:.6f})*v{i}" for i, k in enumerate(names)
                 if abs(terms[k][j]) > 1e-4]
        if not parts:
            continue
        _driver(pb, "rotation_euler", j, "+".join(parts), [(f"v{i}", arm, path(ctrl, k)) for i, k in enumerate(names)])


# Calibrated in calibrate(): the pole angle that makes the elbow / knee point at its pole.
POLE_ANGLE_ARM = {"left": -174 * D2R, "right": -6 * D2R}
POLE_ANGLE_LEG = {"left": -94 * D2R, "right": -86 * D2R}
