# motion

Annie's body is animated by hand, in Blender, and shipped as one motion pack:
`assets/clips/annie-blender` (63 clips, CC0-1.0). This folder is the pipeline that makes it:
headless Blender builds a control rig over the avatar's own VRM armature, keys every clip from data,
checks every frame for collisions on the deformed mesh, fixes them, and bakes the result to the VRM
humanoid bones in the format of [docs/contracts.md](../docs/contracts.md) ("Motion packs").

The runtime that plays the pack (inertialized switches, gesture timing on stressed syllables, the
collision guard, spring-bone cloth) lives in `packages/core/src/` (`character.js`, `gestures.js`,
`body-collide.js`, `cloth.js`).

## The Blender rig

Hand-keyed animation on a control rig built headless over the hero avatar's own VRM armature
(pixiv VRoid AvatarSample_B, `assets/avatars/annie-b/model.vrm`; `ANNIE_AVATAR=<path>` overrides),
keyframes kept as data, every frame collision-checked on the deformed mesh and fixed, baked to the
humanoid bones. CC0-1.0 (authored in this repo; the VRM Add-on is only the importer).

The outfit is the one the stage shows: the avatar's `hide` entry in `assets/avatars/index.json`
(annie-b: `{materials: ["Tops_01_CLOTH"], springs: "CoatSkirt"}`, no varsity jacket) is applied right
after import: faces with those materials are deleted (bmesh) before anything is built from the mesh,
and bones matching `springs` no longer count as torso. `ANNIE_OUTFIT=full` keeps the jacket.

| file | what |
|---|---|
| `blender/rig.py` | imports the VRM (VRM Add-on for Blender) and builds the control rig (below) |
| `blender/poses.py` | the key-pose vocabulary: `BASE` (relaxed contrapposto), talk rests `REST_LOW` / `REST_MID`, clasp / hold poses, `Seq` key builder, mirror / heel / fist / point helpers (plain Python) |
| `blender/clips.py` | idles, fidgets, listening loops, semantic gestures, dance, talk-rest holds (the animation itself, as data) |
| `blender/phrases.py` | the co-speech gesture phrase library (`layer: "gesture"`, prep / stroke / retract markers, pre-stroke holds) |
| `blender/animate.py` | keys the controls: Bezier (auto-clamped) F-curves, per-key easing, per-group lag, Cycles for loops, then a per-frame [1, 2, 1] cushion (below) |
| `blender/collide.py` | mesh collision QA on the deformed avatar (below) |
| `blender/fix.py` | the collision solver: pushes the arm IK targets / elbow poles out, temporally smoothed |
| `blender/export.py` | per clip: key, solve, bake, write the clip JSON + `motion/raw/blender/stats/<clip>.json` |
| `blender/pack.py` | pack.json from the clip files + clips.py metadata, and the before / after collision table (plain Python) |
| `blender/build.sh` | the whole pack in parallel Blender workers, then pack.py and the asset gate |
| `blender/poselab.py` | collision numbers + suggested corrections for static key poses (authoring aid) |
| `blender/fkcheck.py` | measures baked clip JSONs (any pack, e.g. an older one) on the current avatar mesh by FK: the same numbers as export.py |
| `blender/pops.py` | velocity steps in baked clips, counted like the stage's body probe (plain Python) |
| `blender/preview.mjs` | per-clip mp4 + contact sheet in the real stage (`STAGE=http://127.0.0.1:8080`) |

Rig (all non-deform bones in the same armature; the deform bones only follow constraints and drivers):
`CTRL_cog` (parent of the hips: moves the body over the planted feet), `CTRL_torso` props
bend_fwd / bend_side / twist spread over spine / chest / upperChest, `CTRL_head` props pitch / yaw /
tilt spread neck 35% / head 65% (plus an optional Damped Track aim at `CTRL_look`), arm IK
(`CTRL_hand_L/R` wrist targets and `CTRL_elbow_L/R` poles, children of the upper chest; a hidden MCH
chain solves with the elbow locked to a hinge, the deform arm copies it and adds the forearm twist),
`FIX_hand_L/R` / `FIX_elbow_L/R` between the upper chest and those controls (keyed only by the
collision solver), wrist props flex / dev / twist (supination split 50/50 forearm / hand), finger props
curl / spread / thumb_curl / thumb_in + per-finger curl offsets driving all 30 finger joints, leg IK
(`CTRL_foot_L/R` planted in world space, `CTRL_knee_L/R` poles, knee hinge with no hyperextension,
feet copy the control's rotation, so heel lifts are a foot-control pitch + toe bend).

Collision QA (`collide.py`). Every frame the armature-deformed Body / Face / Hair meshes are
evaluated (MToon outline modifiers off) and split by dominant vertex weight; faces the alpha-masked
outfit textures cut away (VRoid keeps invisible geometry, e.g. an unused long coat skirt) are
dropped. Moving points (subsampled on an 8 mm grid): hand + fingers, forearm, elbow end of the upper
arm. Obstacles: torso (skin, crop top, necklace, skirt, bust / skirt spring bones), each thigh +
shin, the head (face + the hair that is rigid with the head; the spring-boned twin-tails are left to
the runtime), the other hand. Torso, legs and head are star-shaped about an axis (spine polyline
extended below the skirt hem, leg bones, head centre): a point is inside when a ray from its axis
point through it still hits the part beyond it, and the depth is the distance to the *outermost* hit,
which is robust to layered clothing. Classes and tolerances (skin is firm): hands, fingers and
forearms are hard (target 0 frames deeper than 5 mm); the elbow end may press 5 mm into the waist;
during an intentional contact the forearm may press 5 mm into what the hand touches. The same rays
give the clearance (`gap_hand` / `gap_elbow`, median per clip): resting arms are authored to lightly
touch the waist and skirt sides (hands 2-4 mm off the skirt, elbows ~2.5 cm off the waist in the
idles). Contacts on the chest read the outermost surface over a +-6 deg cone, so the necklace's heart
pendant is the surface a hand rests on (as the runtime's 5.6 deg polar maps see it).

Fix (`fix.py`), per clip: (1) intentional contacts (`contacts` on a clip: hand to chest, finger to
chin, hair tuck) are attached: the nearest point of the hand is brought to `gap` from the surface;
(2) penetration is pushed out only: the wrist target moves by `dir * (depth + margin)` (forearm
points split between wrist and elbow by where they sit), the elbow pole rotates about the
shoulder-wrist axis to swing the elbow, hand-vs-hand contacts separate along the line between the
hands (clasped-hand clips set `hands_touch`); per-frame pushes are dilated +-3 frames and
Gaussian-smoothed (no jitter, eases in before a contact), accumulated into dense keys on the FIX
bones, re-measured, up to 10 passes, keeping the best iterate, wrist corrections capped at 8 cm;
(3) contacts re-settled. Non-loop clips keep their first / last frames exact (the base pose is
authored collision-free). `motion/raw/blender/stats/<clip>.json` holds before (as authored) and after
numbers; `pack.py` prints the table.

Cushion (`animate.smooth`, on by default, `smooth: 0` on a clip turns it off): after keying, every
control curve is resampled per frame and run through one [1, 2, 1] / 4 pass (loops wrap, one-shots
keep their first and last frame). Bezier keys are only C1, so the acceleration jumps at every key,
worst where a fast stroke lands in a hold: the runtime's "velocity step" pops. The pass spreads each
jump over three frames (gain 0.93 at 2.5 Hz, 0.75 at 5 Hz) and leaves holds and contacts in place;
the collision solver runs after it, so the numbers are for what ships.

Export: for each humanoid bone the world rotation delta from rest, D = R_pose R_rest^T (independent of
Blender's bone rolls), is taken from Blender world (Z up, facing -Y) to VRM 1.0 space
((x, y, z) -> (x, z, -y)) and made local along the VRM humanoid hierarchy (D_parent^T D); hips_position
is the hips head's offset from rest. Checked numerically: re-running three-vrm's normalized FK on the
exported quaternions over the glb's own rest joints reproduces Blender's joint positions to 0.2 mm. export.py also flags ankle travel (feet must not slide unless the clip
steps), stick-straight elbows (targets out of reach) and loop seams.

```bash
# once: the VRM Add-on for Blender as a user extension (Blender 4.2+ / 5.x)
curl -LO https://github.com/saturday06/VRM-Addon-for-Blender/releases/download/v4.7.2/VRM_Addon_for_Blender-Extension-4_7_2.zip
blender -b --python-expr "import bpy; bpy.ops.extensions.package_install_files(filepath='VRM_Addon_for_Blender-Extension-4_7_2.zip', repo='user_default', enable_on_install=True); bpy.ops.wm.save_userpref()"
motion/blender/build.sh 12                   # all 63 clips (~6 min on 12 workers), pack.json, the gate
blender -b -y --python motion/blender/export.py -- --only wave,clap          # a few clips (rewrites pack.json; --no-pack skips it)
blender -b -y --python motion/blender/export.py -- --only wave --out /tmp/qa --no-fix   # as authored
blender -b -y --python motion/blender/poselab.py -- BASE,REST_MID,g_offer_r@stroke     # static pose check
python3 motion/blender/pack.py               # manifest + collision table only
python3 motion/blender/pops.py               # velocity steps (0 in the pack), worst acceleration per clip
blender -b -y --python motion/blender/fkcheck.py -- --pack /tmp/old/assets/clips/annie-blender --src /tmp/old/motion/blender
node motion/blender/preview.mjs /tmp/blender-qa "body=annie-blender&talk=annie-blender" wave,g_offer_r,talk_calm_1
```

Pack contents (63 clips): `idle_a/b/c` (loops, relaxed contrapposto, weight shifts), `fidget_hair /
sleeve / rock / clasp / glance / stretch / hips` (`kind: "fidget"`, one-shots that start and end on
the idle base pose; `fidget_hips` puts both palms on her hips), `listen_a/b`, the semantic gestures
(`wave clap shrug think laugh sad_slump surprised_recoil bow point_self excited_bounce`, `dance` as
`kind: "action"`, 120 bpm with its dips on 0.25 + 0.5 k s to match demo/beat.py), talk-rest holds
`talk_calm_1..3` (`rest: "low"`) and `talk_animated_1..3`, `talk_excited_1..2` (`rest: "mid"`), and 32
gesture phrases `g_*` (`layer: "gesture"`, `kind` beat / deictic / metaphoric / iconic / emblem,
`hands`, `energy`, `rest`, `prep_end` / `stroke` / `retract_start`, `when`), each starting and ending
exactly on its rest. Multi-stroke phrases (`strokes: [t1, t2, ...]`, `strokes[0] == stroke`, the hand
rises a little between beats instead of going home): `g_beats3_r/l`, `g_beats2_both`, `g_list3_l`
(counting on the fingers). Short beats (0.7-0.85 s): `g_tap_r/l`, `g_tap_both`, `g_flick_small_r`.
Pre-stroke holds: the runtime scheduler parks a phrase at `prep_end` (and a multi-stroke phrase at
`strokes[k] - (stroke - prep_end)`, `phrases.hold_points`) and then time-warps the stroke, so those
points must be still in the clip itself: a beat's lift already turns there (the between-beat rises
are keyed on the hold points); where the hand is still moving at the key (points, offers, flicks,
hand to chest) `phrases._hovered` repeats the key 60 ms earlier. The talk rests: `REST_LOW` hands hang on the skirt
sides; `REST_MID` holds the hands in front of the belt with the elbows lightly on the waist.

Authoring conventions (docstrings in `poses.py` / `clips.py` / `phrases.py`): wrist and pole
positions are metres in the upper chest's frame at rest (x = her left, y = up, z = forward); keep wrist
targets within ~93% of the arm's reach (soft elbows: an IK that locks straight, e.g. arms overhead or
knees in a hop, pops when it unlocks; airborne feet rise with the hips); when the elbow pole has to go
from behind her to in front (a hand to the chest or mouth), route it around the side with an
intermediate key, never through the shoulder-wrist line (the arm plane flips: an upper-arm twist
pop); hanging hands rest on the skirt sides, never inside; when the torso pitches, hanging hands need
moving forward in the chest frame or they swing back into the skirt; every gesture starts and ends on
`BASE`; head, wrists and fingers trail the arms by a few frames (`lag`). `character.js` keeps
`layer: "talk"` clips out of the body pools, so one pack serves both `?body=` and `?talk=`.
