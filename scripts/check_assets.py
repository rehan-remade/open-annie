#!/usr/bin/env python3
"""open-annie asset license gate (python3 stdlib only; runs in CI).

Checks, for every assets/avatars/<id>/ directory:
  * avatar.json exists and has the required fields
  * the model file's sha256 and byte size match the manifest
  * the file is a valid GLB whose JSON chunk carries VRM 1.0 (VRMC_vrm) or VRM 0.x (VRM) meta
  * the embedded meta permits redistribution
      - VRM 1.0: meta.allowRedistribution must be true (the spec default is false)
      - VRM 0.x: meta.licenseName must not be 'Redistribution_Prohibited'
  * the manifest license is in the allowlist, and is consistent with the embedded meta
    (e.g. a manifest may only claim CC0-1.0 for a VRM 0.x file whose licenseName is CC0)
  * manifest vrm_version and embedded_meta match the file (catches silent file swaps)
  * lipsync/emotion expressions exist and their binds point at real morph targets
  * the core humanoid bones are mapped

For every assets/clips/<pack>/ directory: pack.json must exist. JSON motion packs
(pack.json with a "clips" map, see docs/contracts.md) must also have:
  * a pack licence in the motion allowlist, authors and source URLs
  * one manifest entry per clip file (no unlisted .json files) whose sha256/bytes match
  * clip files that parse, with fps, duration, loop, bones (VRM humanoid names, 4 floats
    per frame, unit quaternions) and hips_position (3 floats per frame)
Every .vrma anywhere under assets/ is rejected if it contains Mixamo rig node names
('mixamorig'), because Mixamo animation data may not be redistributed.

assets/avatars/index.json (if present) must reference existing avatars and mark exactly
one default.

Exit status is 0 when everything passes, 1 otherwise. Warnings never fail the gate.

Usage: python3 scripts/check_assets.py [--root PATH]
"""

import argparse
import hashlib
import json
import os
import struct
import sys

LICENSE_ALLOWLIST = {"CC0-1.0", "VRoid-AvatarSample", "CC-BY-4.0"}
# Motion data (JSON packs): permissive licences, plus the CMU Graphics Lab mocap terms
# ("This dataset of motions is free for all uses", mocap.cs.cmu.edu).
MOTION_LICENSE_ALLOWLIST = {"CC0-1.0", "CC-BY-4.0", "Apache-2.0", "MIT", "CMU-Graphics-Lab-Mocap"}
PACK_REQUIRED_FIELDS = ["id", "license", "license_url", "authors", "sources", "clips"]
CLIP_ENTRY_FIELDS = ["file", "loop", "duration", "sha256", "bytes"]

REQUIRED_FIELDS = [
    "id", "name", "file", "sha256", "bytes", "source_url", "license", "license_url",
    "author", "vrm_version", "embedded_meta", "expressions",
    "morph_targets_max_per_primitive",
]

# Expressions a talking, emoting avatar must have (VRM 1.0 preset names).
REQUIRED_EXPRESSIONS = ["aa", "ih", "ou", "ee", "oh", "blink",
                        "happy", "angry", "sad", "relaxed"]
# 'surprised' is required for VRM 1.0; VRM 0.x has no such preset, so a custom
# group whose name is 'surprised' (any case) is accepted and absence is a warning.
SURPRISED = "surprised"

VRM0_PRESET_TO_VRM1 = {
    "a": "aa", "i": "ih", "u": "ou", "e": "ee", "o": "oh",
    "joy": "happy", "angry": "angry", "sorrow": "sad", "fun": "relaxed",
    "blink": "blink", "blink_l": "blinkLeft", "blink_r": "blinkRight",
    "neutral": "neutral", "lookup": "lookUp", "lookdown": "lookDown",
    "lookleft": "lookLeft", "lookright": "lookRight",
}

REQUIRED_BONES = [
    "hips", "spine", "chest", "neck", "head",
    "leftUpperArm", "leftLowerArm", "leftHand",
    "rightUpperArm", "rightLowerArm", "rightHand",
    "leftUpperLeg", "leftLowerLeg", "leftFoot",
    "rightUpperLeg", "rightLowerLeg", "rightFoot",
]

VRM_HUMAN_BONES = set(REQUIRED_BONES) | {
    "upperChest", "leftShoulder", "rightShoulder", "leftToes", "rightToes", "leftEye", "rightEye", "jaw"} | {
    "%s%s%s" % (s, f, j) for s in ("left", "right")
    for f, joints in (("Thumb", ("Metacarpal", "Proximal", "Distal")),) + tuple(
        (f, ("Proximal", "Intermediate", "Distal")) for f in ("Index", "Middle", "Ring", "Little"))
    for j in joints}

SIZE_WARN_BYTES = 20 * 1024 * 1024
SIZE_FAIL_BYTES = 90 * 1024 * 1024  # stay well below GitHub's 100 MB per-file hard limit

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A


class GLBError(Exception):
    pass


def read_glb_json(path):
    """Return the parsed JSON chunk of a GLB (binary glTF 2.0) file."""
    with open(path, "rb") as f:
        header = f.read(20)
        if len(header) < 20:
            raise GLBError("file too small to be a GLB")
        magic, version, length = struct.unpack_from("<4sII", header, 0)
        if magic != GLB_MAGIC:
            raise GLBError("bad GLB magic %r (not a binary glTF)" % magic)
        if version != 2:
            raise GLBError("unsupported GLB version %d" % version)
        chunk_len, chunk_type = struct.unpack_from("<II", header, 12)
        if chunk_type != CHUNK_JSON:
            raise GLBError("first GLB chunk is not JSON")
        raw = f.read(chunk_len)
        if len(raw) != chunk_len:
            raise GLBError("truncated JSON chunk")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise GLBError("JSON chunk does not parse: %s" % e)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def morph_count(gltf, mesh_index):
    """Number of morph targets on a mesh (all primitives of a mesh share the count)."""
    meshes = gltf.get("meshes", [])
    if not isinstance(mesh_index, int) or not 0 <= mesh_index < len(meshes):
        return None
    prims = meshes[mesh_index].get("primitives", [])
    return min((len(p.get("targets", [])) for p in prims), default=0)


def max_morphs_per_primitive(gltf):
    counts = [len(p.get("targets", []))
              for m in gltf.get("meshes", []) for p in m.get("primitives", [])]
    return max(counts, default=0)


def valid_binds_vrm0(gltf, binds):
    ok = 0
    for b in binds or []:
        n = morph_count(gltf, b.get("mesh"))
        idx = b.get("index")
        if n is not None and isinstance(idx, int) and 0 <= idx < n and b.get("weight", 0) > 0:
            ok += 1
    return ok


def valid_binds_vrm1(gltf, binds):
    nodes = gltf.get("nodes", [])
    ok = 0
    for b in binds or []:
        node = b.get("node")
        if not isinstance(node, int) or not 0 <= node < len(nodes):
            continue
        n = morph_count(gltf, nodes[node].get("mesh"))
        idx = b.get("index")
        if n is not None and isinstance(idx, int) and 0 <= idx < n and b.get("weight", 0) > 0:
            ok += 1
    return ok


def vrm_info(gltf):
    """Return (version, meta, {expression_name: valid_bind_count}, set(bones))."""
    ext = gltf.get("extensions", {}) or {}
    if "VRMC_vrm" in ext:
        v = ext["VRMC_vrm"]
        exprs = {}
        e = v.get("expressions", {}) or {}
        for group in ("preset", "custom"):
            for name, x in (e.get(group, {}) or {}).items():
                exprs[name] = valid_binds_vrm1(gltf, x.get("morphTargetBinds"))
        bones = set((v.get("humanoid", {}) or {}).get("humanBones", {}) or {})
        return "1.0", v.get("meta") or {}, exprs, bones
    if "VRM" in ext:
        v = ext["VRM"]
        exprs = {}
        groups = (v.get("blendShapeMaster", {}) or {}).get("blendShapeGroups", []) or []
        for g in groups:
            preset = (g.get("presetName") or "unknown").lower()
            name = VRM0_PRESET_TO_VRM1.get(preset) if preset != "unknown" else g.get("name", "")
            if not name:
                continue
            exprs[name] = max(exprs.get(name, 0), valid_binds_vrm0(gltf, g.get("binds")))
        bones = {b.get("bone") for b in (v.get("humanoid", {}) or {}).get("humanBones", []) or []}
        return "0.x", v.get("meta") or {}, exprs, bones
    return None, None, {}, set()


def check_meta(version, meta, manifest_license, errors, warnings):
    """Embedded-meta redistribution rules and manifest/meta consistency."""
    if version == "1.0":
        if meta.get("allowRedistribution") is not True:
            errors.append("embedded VRMC_vrm.meta.allowRedistribution is not true "
                          "(redistribution forbidden or unspecified)")
        if not meta.get("licenseUrl"):
            errors.append("embedded VRMC_vrm.meta.licenseUrl is missing")
        if meta.get("avatarPermission") not in (None, "everyone"):
            warnings.append("avatarPermission is %r (not 'everyone')" % meta.get("avatarPermission"))
        cc0_url = "creativecommons.org/publicdomain/zero"
        if manifest_license == "CC0-1.0" and not any(
                cc0_url in str(meta.get(k) or "") for k in ("licenseUrl", "otherLicenseUrl")):
            errors.append("manifest claims CC0-1.0 but VRM 1.0 meta licenseUrl/otherLicenseUrl "
                          "does not point at CC0")
    elif version == "0.x":
        lic = meta.get("licenseName")
        if lic == "Redistribution_Prohibited":
            errors.append("embedded VRM.meta.licenseName is 'Redistribution_Prohibited'")
        if lic in (None, ""):
            errors.append("embedded VRM.meta.licenseName is missing")
        if manifest_license == "CC0-1.0" and lic != "CC0":
            errors.append("manifest claims CC0-1.0 but embedded licenseName is %r" % lic)
        if manifest_license == "CC-BY-4.0" and lic not in ("CC_BY", "Other"):
            errors.append("manifest claims CC-BY-4.0 but embedded licenseName is %r" % lic)
        if lic == "Other" and manifest_license != "VRoid-AvatarSample":
            errors.append("embedded licenseName is 'Other'; only an explicit author grant "
                          "(VRoid-AvatarSample) may be used with it")
        if meta.get("allowedUserName") not in (None, "Everyone"):
            warnings.append("allowedUserName is %r (not 'Everyone')" % meta.get("allowedUserName"))


def check_avatar(root, d):
    rel = os.path.relpath(d, root)
    errors, warnings = [], []
    row = {"id": os.path.basename(d), "license": "?", "vrm": "?", "MB": "?",
           "morphs": "?", "exprs": "?"}
    man_path = os.path.join(d, "avatar.json")
    if not os.path.isfile(man_path):
        return row, ["%s: missing avatar.json" % rel], warnings
    try:
        with open(man_path, encoding="utf-8") as f:
            man = json.load(f)
    except ValueError as e:
        return row, ["%s/avatar.json: invalid JSON: %s" % (rel, e)], warnings

    for k in REQUIRED_FIELDS:
        if k not in man:
            errors.append("avatar.json missing field '%s'" % k)
    if man.get("id") and man["id"] != os.path.basename(d):
        errors.append("avatar.json id %r does not match directory name" % man["id"])
    lic = man.get("license")
    row["license"] = lic or "?"
    if lic not in LICENSE_ALLOWLIST:
        errors.append("license %r not in allowlist %s" % (lic, sorted(LICENSE_ALLOWLIST)))

    model = os.path.join(d, man.get("file") or "model.vrm")
    if not os.path.isfile(model):
        errors.append("model file %s not found" % os.path.relpath(model, root))
        return row, ["%s: %s" % (rel, e) for e in errors], warnings

    size = os.path.getsize(model)
    row["MB"] = "%.1f" % (size / 1e6)
    if man.get("bytes") != size:
        errors.append("bytes mismatch: manifest %r, file %d" % (man.get("bytes"), size))
    digest = sha256_file(model)
    if man.get("sha256") != digest:
        errors.append("sha256 mismatch: manifest %r, file %s" % (man.get("sha256"), digest))
    if size > SIZE_FAIL_BYTES:
        errors.append("file is %.1f MB (limit %d MB)" % (size / 1e6, SIZE_FAIL_BYTES // 2**20))
    elif size > SIZE_WARN_BYTES:
        warnings.append("file is %.1f MB (> 20 MB preferred max)" % (size / 1e6))

    try:
        gltf = read_glb_json(model)
    except (GLBError, OSError) as e:
        errors.append("cannot parse GLB: %s" % e)
        return row, ["%s: %s" % (rel, e) for e in errors], warnings

    version, meta, exprs, bones = vrm_info(gltf)
    if version is None:
        errors.append("no VRMC_vrm or VRM extension in glTF JSON")
        return row, ["%s: %s" % (rel, e) for e in errors], warnings
    row["vrm"] = version
    if man.get("vrm_version") != version:
        errors.append("vrm_version mismatch: manifest %r, file %r" % (man.get("vrm_version"), version))
    if man.get("embedded_meta") != meta:
        errors.append("embedded_meta in avatar.json does not match the file's meta")
    check_meta(version, meta, lic, errors, warnings)

    morphs = max_morphs_per_primitive(gltf)
    row["morphs"] = str(morphs)
    if man.get("morph_targets_max_per_primitive") != morphs:
        errors.append("morph_targets_max_per_primitive mismatch: manifest %r, file %d"
                      % (man.get("morph_targets_max_per_primitive"), morphs))

    bound = {k for k, n in exprs.items() if n > 0}
    missing = [e for e in REQUIRED_EXPRESSIONS if e not in bound]
    if missing:
        errors.append("expressions missing or with no valid morph binds: %s" % ", ".join(missing))
    has_surprised = any(k.lower() == SURPRISED for k in bound)
    if not has_surprised:
        if version == "1.0":
            errors.append("expression 'surprised' missing or unbound")
        else:
            warnings.append("no 'surprised' expression (VRM 0.x has no such preset)")
    row["exprs"] = "%d%s" % (len(bound), "" if has_surprised else " (no surprised)")

    missing_bones = [b for b in REQUIRED_BONES if b not in bones]
    if missing_bones:
        errors.append("humanoid bones missing: %s" % ", ".join(missing_bones))

    return (row, ["%s: %s" % (rel, e) for e in errors],
            ["%s: %s" % (rel, w) for w in warnings])


def check_index(root, avatar_ids):
    path = os.path.join(root, "assets", "avatars", "index.json")
    if not os.path.isfile(path):
        return ["assets/avatars/index.json: missing"] if avatar_ids else []
    errors = []
    try:
        with open(path, encoding="utf-8") as f:
            idx = json.load(f)
    except ValueError as e:
        return ["assets/avatars/index.json: invalid JSON: %s" % e]
    if not isinstance(idx, list):
        return ["assets/avatars/index.json: must be a JSON array"]
    defaults = [e for e in idx if isinstance(e, dict) and e.get("default") is True]
    if len(defaults) != 1:
        errors.append("assets/avatars/index.json: exactly one entry must have default: true "
                      "(found %d)" % len(defaults))
    listed = set()
    for e in idx:
        if not isinstance(e, dict) or not all(k in e for k in ("id", "name", "path", "default")):
            errors.append("assets/avatars/index.json: entry %r needs id, name, path, default" % e)
            continue
        listed.add(e["id"])
        if e["id"] not in avatar_ids:
            errors.append("assets/avatars/index.json: %r has no assets/avatars/%s/ dir"
                          % (e["id"], e["id"]))
        if not os.path.isfile(os.path.join(root, e["path"])):
            errors.append("assets/avatars/index.json: path %r does not exist" % e["path"])
    for a in sorted(set(avatar_ids) - listed):
        errors.append("assets/avatars/index.json: avatar %r is not listed" % a)
    return errors


def check_motion_clip(path, rel, entry, errors):
    """Structural check of one JSON motion clip against its manifest entry."""
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except ValueError as e:
        errors.append("%s: invalid JSON: %s" % (rel, e))
        return
    for k in ("fps", "duration", "loop", "bones", "hips_position"):
        if k not in c:
            errors.append("%s: missing field '%s'" % (rel, k))
            return
    fps = c["fps"]
    frames = c.get("frames") or int(round(c["duration"] * fps)) + (0 if c["loop"] else 1)
    if not isinstance(c["bones"], dict) or not c["bones"]:
        errors.append("%s: bones must be a non-empty map" % rel)
        return
    for b, arr in c["bones"].items():
        if b not in VRM_HUMAN_BONES:
            errors.append("%s: unknown VRM humanoid bone %r" % (rel, b))
            continue
        if len(arr) != frames * 4:
            errors.append("%s: bone %s has %d floats, expected %d" % (rel, b, len(arr), frames * 4))
            continue
        for i in range(0, len(arr), 4):
            n = sum(x * x for x in arr[i:i + 4])
            if abs(n - 1) > 0.01:
                errors.append("%s: bone %s frame %d is not a unit quaternion (|q|^2=%.3f)" % (rel, b, i // 4, n))
                break
    if len(c["hips_position"]) != frames * 3:
        errors.append("%s: hips_position has %d floats, expected %d" % (rel, len(c["hips_position"]), frames * 3))
    if bool(c["loop"]) != bool(entry.get("loop")):
        errors.append("%s: loop flag disagrees with pack.json" % rel)
    if abs(float(c["duration"]) - float(entry.get("duration", -1))) > 1e-3:
        errors.append("%s: duration disagrees with pack.json" % rel)


def check_motion_pack(root, d, pack, errors, warnings):
    rel = os.path.relpath(d, root)
    for k in PACK_REQUIRED_FIELDS:
        if k not in pack:
            errors.append("%s/pack.json: missing field '%s'" % (rel, k))
    if pack.get("id") and pack["id"] != os.path.basename(d):
        errors.append("%s/pack.json: id %r does not match directory name" % (rel, pack["id"]))
    lic = pack.get("license")
    if lic not in MOTION_LICENSE_ALLOWLIST:
        errors.append("%s/pack.json: license %r not in motion allowlist %s"
                      % (rel, lic, sorted(MOTION_LICENSE_ALLOWLIST)))
    if pack.get("license_caveat"):
        warnings.append("%s: licence caveat: %s" % (rel, pack["license_caveat"][:160] + "..."))
    clips = pack.get("clips") or {}
    listed = set()
    for name, e in clips.items():
        for k in CLIP_ENTRY_FIELDS:
            if k not in e:
                errors.append("%s/pack.json: clip %r missing '%s'" % (rel, name, k))
        fn = e.get("file")
        if not fn:
            continue
        listed.add(fn)
        p = os.path.join(d, fn)
        if not os.path.isfile(p):
            errors.append("%s/pack.json: clip %r file %s not found" % (rel, name, fn))
            continue
        if e.get("license") and e["license"] not in MOTION_LICENSE_ALLOWLIST:
            errors.append("%s/%s: license %r not in motion allowlist" % (rel, fn, e["license"]))
        if os.path.getsize(p) != e.get("bytes"):
            errors.append("%s/%s: bytes mismatch: manifest %r, file %d" % (rel, fn, e.get("bytes"), os.path.getsize(p)))
        if sha256_file(p) != e.get("sha256"):
            errors.append("%s/%s: sha256 mismatch with pack.json" % (rel, fn))
        check_motion_clip(p, "%s/%s" % (rel, fn), e, errors)
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json") and fn != "pack.json" and fn not in listed:
            errors.append("%s/%s: not listed in pack.json (every motion file needs a manifest entry)" % (rel, fn))


def check_clips(root, warnings=None):
    warnings = [] if warnings is None else warnings
    errors, rows = [], []
    clips = os.path.join(root, "assets", "clips")
    if os.path.isdir(clips):
        for name in sorted(os.listdir(clips)):
            d = os.path.join(clips, name)
            if not os.path.isdir(d):
                continue
            ok = os.path.isfile(os.path.join(d, "pack.json"))
            if not ok:
                errors.append("assets/clips/%s: missing pack.json" % name)
            else:
                try:
                    with open(os.path.join(d, "pack.json"), encoding="utf-8") as f:
                        pack = json.load(f)
                except ValueError as e:
                    errors.append("assets/clips/%s/pack.json: invalid JSON: %s" % (name, e))
                else:
                    if isinstance(pack, dict) and "clips" in pack:
                        check_motion_pack(root, d, pack, errors, warnings)
            rows.append(name)
    # Any .vrma under assets/ (not only clips) is scanned for Mixamo rigs.
    # assets/clips/index.json: the packs the browser loads must exist and be motion packs.
    idx_path = os.path.join(clips, "index.json")
    if os.path.isfile(idx_path):
        try:
            with open(idx_path, encoding="utf-8") as f:
                idx = json.load(f)
        except ValueError as e:
            errors.append("assets/clips/index.json: invalid JSON: %s" % e)
            idx = {}
        listed = list(idx.get("body") or []) + ([idx["talk"]] if idx.get("talk") else []) + list((idx.get("takes") or {}).values())
        for pid in listed:
            if not os.path.isfile(os.path.join(clips, pid, "pack.json")):
                errors.append("assets/clips/index.json: pack %r has no assets/clips/%s/pack.json" % (pid, pid))
    for dirpath, _dirs, files in os.walk(os.path.join(root, "assets")):
        for fn in files:
            if not fn.lower().endswith(".vrma"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            try:
                gltf = read_glb_json(p)
            except (GLBError, OSError) as e:
                errors.append("%s: cannot parse GLB: %s" % (rel, e))
                continue
            names = [n.get("name", "") for n in gltf.get("nodes", [])]
            names += [a.get("name", "") for a in gltf.get("animations", [])]
            bad = sorted({n for n in names if "mixamorig" in (n or "").lower()})
            if bad or "mixamorig" in json.dumps(gltf).lower():
                errors.append("%s: contains Mixamo rig names (%s); Mixamo data may not be "
                              "redistributed" % (rel, ", ".join(bad[:5]) or "in JSON"))
            if "VRMC_vrm_animation" not in (gltf.get("extensions") or {}):
                errors.append("%s: no VRMC_vrm_animation extension" % rel)
    return rows, errors


def print_table(rows):
    cols = ["id", "license", "vrm", "MB", "morphs", "exprs", "status"]
    width = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    line = "  ".join(c.ljust(width[c]) for c in cols)
    print(line)
    print("  ".join("-" * width[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(width[c]) for c in cols))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    help="repository root (default: parent of scripts/)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    all_errors, all_warnings, rows = [], [], []
    avatars_dir = os.path.join(root, "assets", "avatars")
    avatar_ids = []
    if os.path.isdir(avatars_dir):
        for name in sorted(os.listdir(avatars_dir)):
            d = os.path.join(avatars_dir, name)
            if not os.path.isdir(d):
                continue
            avatar_ids.append(name)
            row, errs, warns = check_avatar(root, d)
            row["status"] = "FAIL" if errs else "ok"
            rows.append(row)
            all_errors += errs
            all_warnings += warns
    all_errors += check_index(root, avatar_ids)
    packs, clip_errors = check_clips(root, all_warnings)
    all_errors += clip_errors

    print("open-annie asset gate  (root: %s)" % root)
    print()
    if rows:
        print_table(rows)
    else:
        print("(no avatars under assets/avatars/)")
    print()
    print("clip packs: %s" % (", ".join(packs) if packs else "(none)"))
    for w in all_warnings:
        print("WARN  " + w)
    for e in all_errors:
        print("ERROR " + e)
    print()
    sys.stdout.flush()
    if all_errors:
        print("FAIL: %d error(s). Fix the assets or their manifests; see assets/README.md."
              % len(all_errors), file=sys.stderr)
        return 1
    print("PASS: %d avatar(s), %d clip pack(s), %d warning(s)."
          % (len(rows), len(packs), len(all_warnings)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
