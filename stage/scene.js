import * as THREE from "three";
import { applyLook } from "./look.js";

// The set: a lit cyclorama wall with a soft halo behind her, a spotlight pool on
// the floor, a warm key that casts a real soft shadow, and pink/cyan rims.
// Everything is deterministic (no random), so capture frames are reproducible.

const WALL_VS = `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`;
const WALL_FS = `
varying vec2 vUv;
uniform vec3 top, bottom, glow, glow2;
uniform vec2 halo;
void main(){
  vec3 c = mix(bottom, top, smoothstep(0.0, 1.0, vUv.y));
  float d = distance(vUv * vec2(1.6, 1.0), halo * vec2(1.6, 1.0));
  c += glow * pow(smoothstep(0.45, 0.0, d), 1.4) * 0.05;
  c += (glow2 - vec3(1.0)) * smoothstep(0.6, 0.0, distance(vUv, vec2(0.8, 0.8))) * 0.4;
  // horizon line where wall meets floor, softly lit

  gl_FragColor = vec4(c, 1.0);
}`;
const FLOOR_FS = `
varying vec2 vUv;
uniform vec3 base, pool;
void main(){
  float d = distance(vUv, vec2(0.5, 0.5));
  vec3 c = base + vec3(0.02) * smoothstep(0.32, 0.0, d);
  // Fade to the wall's bottom colour far from her, so there's no seam at the horizon.
  c = mix(c, base, smoothstep(0.3, 0.7, d));
  gl_FragColor = vec4(c, 1.0);
}`;

export function createStage(canvas, { capture }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: capture });
  // Capture renders at the page's device pixel ratio (2x = supersampled, downscaled by the screenshot).
  renderer.setPixelRatio(capture ? devicePixelRatio : Math.min(devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xf4f3f1);
  const camera = new THREE.PerspectiveCamera(26, 16 / 9, 0.1, 60);

  const wall = new THREE.Mesh(
    new THREE.PlaneGeometry(16, 7),
    new THREE.ShaderMaterial({
      vertexShader: WALL_VS,
      fragmentShader: WALL_FS,
      uniforms: {
        top: { value: new THREE.Color(0xf4f3f1) },
        bottom: { value: new THREE.Color(0xecebe8) },
        glow: { value: new THREE.Color(0xffffff) },
        glow2: { value: new THREE.Color(0xf3e8ff) },
        halo: { value: new THREE.Vector2(0.47, 0.34) },
      },
      depthWrite: false,
    }),
  );
  wall.position.set(0, 3.2, -3.2);
  scene.add(wall);

  const floorMat = new THREE.ShaderMaterial({
    vertexShader: WALL_VS,
    fragmentShader: FLOOR_FS,
    uniforms: { base: { value: new THREE.Color(0xecebe8) }, pool: { value: new THREE.Color(0x0e0e0e) } },
  });
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(16, 10), floorMat);
  floor.rotation.x = -Math.PI / 2;
  floor.position.z = 1.6;
  scene.add(floor);
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(6, 6), new THREE.ShadowMaterial({ opacity: 0.16 }));
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = 0.002;
  shadow.receiveShadow = true;
  scene.add(shadow);

  scene.add(new THREE.HemisphereLight(0xffffff, 0xd9d6d0, 0.95));
  const key = new THREE.DirectionalLight(0xffffff, 1.05);
  key.position.set(1.6, 3.2, 2.6);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.radius = 6;
  key.shadow.bias = -0.0004;
  Object.assign(key.shadow.camera, { left: -1.5, right: 1.5, top: 2.2, bottom: -0.3, near: 0.5, far: 8 });
  scene.add(key);
  const rimL = new THREE.DirectionalLight(0xffffff, 0.45);
  rimL.position.set(-2.6, 2.0, -2.2);
  scene.add(rimL);
  const rimR = new THREE.DirectionalLight(0xf3e8ff, 0.35);
  rimR.position.set(2.6, 1.8, -2.2);
  scene.add(rimR);

  function resize() {
    const w = innerWidth, h = innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  addEventListener("resize", resize);
  resize();

  // Called once the avatar is in the scene.
  function adopt(root) {
    root.traverse((o) => {
      if (o.isMesh) o.castShadow = true;
    });
    applyLook(root, { enabled: new URLSearchParams(location.search).get("look") !== "off" });
  }
  return { renderer, scene, camera, adopt, wall };
}
