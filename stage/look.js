// Anime cel look for VRoid exports. AvatarSample_A arrives with MToon set to "fully lit,
// white shade" (shadingShift 1, shadeColor #fff), so the toon ramp never shows and she
// reads flat and washed out on a light set. This gives skin, cloth and hair a real
// light/shade split with tinted shades, a soft rim, and slightly crisper outlines.
// Materials are matched by VRoid's name suffixes; anything unknown is left alone.

const LOOKS = {
  face: { toony: 0.45, shift: -0.35, shade: 0xf6d6d2 }, // faces stay soft: no hard terminator on cheeks
  skin: { toony: 0.9, shift: -0.08, shade: 0xf1c8c3, rim: [0xfff0f4, 0.35] },
  cloth: { toony: 0.88, shift: -0.05, shade: 0xdcd5e6, rim: [0xffffff, 0.25], outline: 0.0016 },
  hair: { toony: 0.82, shift: -0.18, shade: 0xb49aac, rim: [0xfff4fa, 0.3] },
};

function kindOf(name) {
  if (/_FACE$|_EYE$/.test(name)) return null; // eyes, lashes, brows, mouth: authored as-is
  if (/Face_00_SKIN/.test(name)) return "face";
  if (/_SKIN$/.test(name)) return "skin";
  if (/_CLOTH$/.test(name)) return "cloth";
  if (/_HAIR_/.test(name)) return "hair";
  return null;
}

export function applyLook(root, { enabled = true } = {}) {
  if (!enabled) return;
  root.traverse((o) => {
    if (!o.isMesh) return;
    for (const m of [].concat(o.material)) {
      const look = LOOKS[kindOf(m.name.replace(/ \(Outline\)$/, ""))];
      if (!look || !("shadingShiftFactor" in m)) continue;
      m.shadingToonyFactor = look.toony;
      m.shadingShiftFactor = look.shift;
      m.shadeColorFactor.setHex(look.shade).convertSRGBToLinear();
      if (look.rim) {
        m.parametricRimColorFactor.setHex(look.rim[0]).convertSRGBToLinear().multiplyScalar(look.rim[1]);
        m.parametricRimFresnelPowerFactor = 4;
        m.parametricRimLiftFactor = 0;
        m.rimLightingMixFactor = 0.6;
      }
      if (look.outline && m.outlineWidthMode !== "none") m.outlineWidthFactor = look.outline;
      m.needsUpdate = true;
    }
  });
}
