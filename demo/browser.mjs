// One place to launch headless Chromium for capture. On WSL2 the RTX GPU is reachable
// through Mesa's D3D12 Gallium driver (ANGLE → GL-EGL); elsewhere, or with ANNIE_GPU=0,
// it falls back to SwiftShader (correct, but ~40x slower with soft shadows).
// Without root, Chromium's missing system libraries (libnss3, libnspr4, libasound2) can be
// unpacked with `apt-get download` + `dpkg -x` into CHROMIUM_LIBS (default below).
import { chromium } from "playwright";
import { existsSync } from "node:fs";
import { homedir } from "node:os";

const LIBS = process.env.CHROMIUM_LIBS ?? `${homedir()}/.local/share/chromium-libs/root/usr/lib/x86_64-linux-gnu`;

export function launch(extra = []) {
  const wsl = existsSync("/dev/dxg") && process.env.ANNIE_GPU !== "0";
  const env = { ...process.env };
  if (existsSync(LIBS)) env.LD_LIBRARY_PATH = [LIBS, env.LD_LIBRARY_PATH].filter(Boolean).join(":");
  if (wsl) Object.assign(env, { GALLIUM_DRIVER: "d3d12", MESA_D3D12_DEFAULT_ADAPTER_NAME: env.MESA_D3D12_DEFAULT_ADAPTER_NAME ?? "NVIDIA", LD_LIBRARY_PATH: `${env.LD_LIBRARY_PATH}:/usr/lib/wsl/lib` });
  const gpu = wsl ? ["--use-gl=angle", "--use-angle=gl-egl", "--ignore-gpu-blocklist", "--enable-gpu"] : ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"];
  return chromium.launch({ env, args: [...gpu, ...extra] });
}
