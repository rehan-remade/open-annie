// Node has no import map: stub three-vrm (mouth.js only needs it to register expressions on a real VRM).
export async function resolve(spec, ctx, next) {
  if (spec === "@pixiv/three-vrm") return { url: "data:text/javascript,export class VRMExpression{};export class VRMExpressionMorphTargetBind{};", shortCircuit: true };
  return next(spec, ctx);
}
