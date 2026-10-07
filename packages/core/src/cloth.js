// Cloth and hair: the avatar's own VRM spring bones, tuned per part and stepped at a
// fixed rate. VRoid ships these stiff and undamped (AvatarSample_B's jacket hem: stiffness
// 0.75, drag 0, and no colliders, so it rings and swings through her legs). Profiles are
// matched by VRoid's J_Sec_* bone names; parts that don't match are left as authored.

const PROFILES = {
  // Pleated skirt: swings on turns and settles; brushes thighs, arms and hands, not through them.
  skirt: { stiffness: 0.36, dragForce: 0.2, gravityPower: 0.08, colliders: /UpperLeg|LowerArm|Hand/ },
  // Jacket hem: heavier fabric, follows the hips a little later than the skirt.
  coat: { stiffness: 0.5, dragForce: 0.28, gravityPower: 0.06, colliders: /UpperLeg|LowerArm|Hand/ },
  // Long twin-tails: looser than authored, a touch of gravity so they hang and trail.
  // Hands and forearms push locks aside (a hand to the mouth meets the lock by her cheek).
  hair: { stiffnessScale: 0.62, dragForce: 0.34, gravityMin: 0.08, colliders: /LowerArm|Hand/ },
};

function partOf(boneName) {
  if (/CoatSkirt/.test(boneName)) return "coat";
  if (/Skirt/.test(boneName)) return "skirt";
  if (/Hair/.test(boneName)) return "hair";
  return null; // bust, hood strings, anything else: as authored
}

export function tuneSprings(vrm, { enabled = true } = {}) {
  const sb = vrm.springBoneManager;
  if (!sb || !enabled) return;
  const groups = [...sb.colliderGroups];
  const groupsOn = (re) => groups.filter((g) => g.colliders.some((c) => re.test(c.parent?.name ?? "")));
  for (const j of sb.joints) {
    const p = PROFILES[partOf(j.bone.name)];
    if (!p) continue;
    const s = j.settings;
    if (p.stiffness != null) s.stiffness = p.stiffness;
    if (p.stiffnessScale != null) s.stiffness *= p.stiffnessScale;
    if (p.dragForce != null) s.dragForce = p.dragForce;
    if (p.gravityPower != null) s.gravityPower = p.gravityPower;
    if (p.gravityMin != null) s.gravityPower = Math.max(s.gravityPower, p.gravityMin);
    if (p.colliders) j.colliderGroups = [...new Set([...j.colliderGroups, ...groupsOn(p.colliders)])];
  }
  sb.setInitState();
}

// vrm.update(dt) with the spring bones stepped `substeps` times at dt/substeps: one big
// Verlet step per 30 fps frame overshoots and jitters; small fixed steps swing smoothly and
// stay deterministic for frame-stepped capture.
export function stepSprings(vrm, dt, substeps = 4) {
  const sb = vrm.springBoneManager;
  if (!sb) return vrm.update(dt);
  vrm.springBoneManager = null;
  vrm.update(dt);
  vrm.springBoneManager = sb;
  const n = Math.max(1, substeps);
  for (let i = 0; i < n; i++) sb.update(dt / n);
}
