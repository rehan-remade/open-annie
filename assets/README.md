# assets

Everything in here is redistributed from this public repo (and from any site that hosts the
stage), so every file has to carry a license that allows that. `scripts/check_assets.py`
enforces it in CI.

```
assets/avatars/index.json        [{id, name, path, default}]; path is relative to the repo root
assets/avatars/<id>/model.vrm    the VRM file
assets/avatars/<id>/avatar.json  license manifest (provenance, hash, embedded meta, expressions)
assets/clips/index.json          installed motion packs, in load priority
assets/clips/<pack>/pack.json    motion pack manifest (licence, sources, per-clip sha256)
assets/clips/<pack>/<clip>.json  retargeted motion (format: docs/contracts.md, "Motion packs")
```

## Motion packs

| pack | clips | source | licence |
|---|---|---|---|
| `annie-blender` | 63: idles, listening loops, Jev's semantic clips (wave, dance, shrug, …), talk-rest loops, 32 co-speech gesture phrases, fidgets | keyed by hand on an IK control rig in headless Blender, collision-checked against Annie's deformed mesh ([motion/README.md](../motion/README.md)) | CC0-1.0 |

30 fps, VRM 1.0 normalized-bone quaternions (format: [docs/contracts.md](../docs/contracts.md),
"Motion packs"). Any clip a pack lacks falls back to the procedural annie-core pack
(`packages/core/src/clips.js`, CC0).

## Avatars

| id | name | license | VRM | size | morphs / primitive | default |
|---|---|---|---|---|---|---|
| `annie-b` | Annie (pixiv AvatarSample_B) | VRoid-AvatarSample (pixiv sample terms, **not** CC0) | 0.x | 15.4 MB | 56 | yes |
| `annie` | Annie classic (pixiv AvatarSample_A) | VRoid-AvatarSample (pixiv sample terms, **not** CC0) | 0.x | 15.1 MB | 56 | no |
| `vivi` | Vivi (pixiv AvatarSample_E) | CC0-1.0 | 0.x | 18.1 MB | 41 | no |

All three are official VRoid Project (pixiv Inc.) sample models: anime style, full VRoid face rig,
54 humanoid bones including fingers, eyes and upperChest, MToon materials. Annie is shown without
AvatarSample_B's varsity jacket: `assets/avatars/index.json` lists the jacket's material and hem
springs under `hide`, and `Character.load` removes them before anything else is built
(`?outfit=full` keeps the jacket).

### annie-b: AvatarSample_B

- Upstream, bytes and licence: the same VRoid Project account, VRoid Studio bundled export and pixiv
  sample-model conditions as AvatarSample_A below; `avatar.json` records the exact URLs, hashes and
  the VRoid Hub licence evidence. Do not relabel it CC0.

### annie: AvatarSample_A

- Upstream: VRoid Hub, VRoid Project account:
  <https://hub.vroid.com/en/characters/2843975675147313744/models/5644550979324015604>
- Bytes: the VRoid Studio bundled export (VRoidStudio-0.14.0), as mirrored at
  <https://github.com/madjin/vrm-samples/raw/e16eb187100149a315ad92c3c9968f1d5baa6c7d/vroid/stable/AvatarSample_A.vrm>.
  The VRoid Hub download needs a login.
- License: pixiv's conditions of use for AvatarSample_A/B/C,
  <https://vroid.pixiv.help/hc/en-us/articles/4402394424089>. pixiv's FAQ
  (<https://vroid.pixiv.help/hc/en-us/articles/4402614652569>) says these models are *not*
  CC0 and their copyright is not waived, but they may be altered and distributed. The
  VRoid Hub license on the official upload (checked 2026-09-27) is: redistribution allow,
  modification allow, credit unnecessary, avatar use by everyone, corporate commercial use allow.
- Embedded meta: VRM 0.x `licenseName: "Other"`, `allowedUserName: "Everyone"`,
  `commercialUssageName: "Allow"`. It does not forbid redistribution. Do not relabel it CC0.

### vivi: Vivi / AvatarSample_E

- Upstream: VRoid Hub, VRoid Project account (title "ビビ"):
  <https://hub.vroid.com/en/characters/945152946522067123/models/1622417912888236740>
- Bytes: the VRoid Studio beta bundled export (VRoidStudio-0.8.1), as mirrored at
  <https://github.com/madjin/vrm-samples/raw/e16eb187100149a315ad92c3c9968f1d5baa6c7d/vroid/beta/Vivi.vrm>
- License: **CC0-1.0**. pixiv's FAQ lists AvatarSample_E, the renamed beta model "Vivi",
  among the "CC0 license models" whose copyright is waived. The embedded meta agrees:
  `licenseName: "CC0"`.

### Expressions (all three avatars)

These are VRM 0.x files. three-vrm maps their presets to VRM 1.0 names:

| VRM 0.x | three-vrm name | annie-b | annie | vivi |
|---|---|---|---|---|
| A I U E O | `aa ih ou ee oh` | yes | yes | yes |
| Blink, Blink_L, Blink_R | `blink blinkLeft blinkRight` | yes | yes | yes |
| Joy / Angry / Sorrow / Fun | `happy angry sad relaxed` | yes | yes | yes |
| (custom) Surprised | `Surprised` (custom expression) | yes | yes | yes |
| (custom) Extra | `Extra` | yes | yes | yes |
| Neutral | `neutral` | bound | bound | empty (no binds) |

VRM 0.x has no `surprised` preset. All three models ship a custom `Surprised` group, so
`faces.js` should fall back to the custom name `Surprised` when the preset is missing.
All binds point at the `Face.baked` mesh; the gate checks that every bind's morph index
exists.

## The gate: `scripts/check_assets.py`

This is a python3 script that uses only the standard library. Run it with
`python3 scripts/check_assets.py`. It exits 1 if anything fails.

For each `assets/avatars/<id>/` it checks that:

1. `avatar.json` exists, has the required fields, and its `id` matches the directory name.
2. The file's `bytes` and `sha256` match the manifest.
3. The file is a valid GLB (glTF 2.0 binary). The script reads the JSON chunk directly.
4. The embedded meta allows redistribution:
   - VRM 1.0 (`extensions.VRMC_vrm.meta`): `allowRedistribution` must be `true`, because the
     spec default is false. `licenseUrl` must be present.
   - VRM 0.x (`extensions.VRM.meta`): `licenseName` must not be `Redistribution_Prohibited`.
5. `license` is in the allowlist `{CC0-1.0, VRoid-AvatarSample, CC-BY-4.0}`
   and agrees with the embedded meta:
   - a CC0-1.0 claim needs VRM0 `licenseName: CC0`, or a VRM1 license URL pointing at CC0;
   - VRM0 `licenseName: Other` is accepted only together with an explicit author grant
     (`VRoid-AvatarSample`).
6. `vrm_version`, `embedded_meta` and `morph_targets_max_per_primitive` match the file, so a
   swapped model is caught.
7. `aa ih ou ee oh blink happy angry sad relaxed` exist with at least one valid morph bind.
   `surprised` is required for VRM 1.0 files. For VRM 0.x files a missing `surprised` only
   produces a warning.
8. The core humanoid bones are mapped: hips, spine, chest, neck, head, upper and lower
   arms, hands, upper and lower legs, feet.
9. Size: the gate warns above 20 MB and fails above 90 MB.

It also checks `assets/avatars/index.json`: exactly one entry must be the default, every
avatar directory must be listed, and every path must exist.

For each `assets/clips/<pack>/` directory, `pack.json` must exist and parse. JSON motion packs must
also have a licence in the motion allowlist (`CC0-1.0 CC-BY-4.0 Apache-2.0 MIT CMU-Graphics-Lab-Mocap`),
authors and sources, one manifest entry per clip file with matching sha256 and bytes, and clip files
with unit-quaternion tracks on known VRM bones; a `license_caveat` is printed as a warning, and every
pack named in `assets/clips/index.json` must exist. Every `.vrma`
under `assets/` is parsed and rejected if any node, animation or JSON contains `mixamorig`,
because Mixamo animation data may not be redistributed. Each `.vrma` must also carry
`VRMC_vrm_animation`.

### Adding an avatar

1. Put the file at `assets/avatars/<id>/model.vrm`.
2. Parse its meta and confirm it allows redistribution.
3. Write `avatar.json`: copy `embedded_meta` verbatim from the file, and compute `sha256`,
   `bytes`, `morph_targets_max_per_primitive` and `expressions`.
4. Add the avatar to `index.json`, then run the gate.

## Candidates considered and rejected

| candidate | why rejected |
|---|---|
| hinzka `VRoid_V110_Female_v1.1.3.vrm` (52 ARKit blendshapes) | The README grants free redistribution, but the **embedded meta says `licenseName: Redistribution_Prohibited`**. The file's meta wins, so the gate rejects it. It is also 23.3 MB. |
| VRM Consortium `Seed-san`, pixiv `VRM1_Constraint_Twist_Sample` (VRM 1.0) | Technically excellent: every VRM 1.0 preset bound, `allowRedistribution: true`, about 11 MB each. Their license is the *VRM Public License 1.0*, which is not in the allowlist. Seed-san also needs credit ("creditNotation: required"). They could be added if `VRM-PL-1.0` is added to the allowlist. |
| 100Avatars R1/R2 (Polygonal Mind, CC0), e.g. Rose, Polydancer, CoolPan | Low-poly (3.5k–5.6k triangles). They have A/I/U/E/O and blink, but **no emotion expressions** (no joy/angry/sorrow/fun). |
| 100Avatars R3 "Jenny" (CC0) | It has visemes and joy/angry/sorrow/fun and is 6.7 MB, but it is a fish-headed character and not right as the hero. |
| AvatarSample_C, Vita (F), Victoria Rubin (G), HairSample_Female, Sendagaya Shino, Sakurada Fumiriya | All pass technically. They were left out to keep the repo small (AvatarSample_B became the default Annie). Vita and Victoria Rubin are CC0 per pixiv's FAQ and are good future additions. Sendagaya Shino and Sakurada Fumiriya embed CC0 but do not appear on pixiv's current CC0 list. |
| Xmas Chibis / Halloween Rising (CC0) | The IPFS gateways (dweb.link, ipfs.io) returned errors, so the files were never inspected. |
