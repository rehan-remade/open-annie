// Stage previews of clips in the real three.js stage (frame-stepped): an mp4 and an 8-frame contact sheet per clip.
// The scripted conversation is pushed out of the way; talk loops play alone on the talk layer while she "speaks".
// Needs the repo static server on :8080. Writes only to OUTDIR (keep it out of demo/frames and demo/out).
//   node motion/blender/preview.mjs OUTDIR "body=annie-blender&talk=annie-blender" clip1,clip2 [--sheet-only] [--tag X]
import { launch } from "../../demo/browser.mjs";
const ROOT = new URL("../../", import.meta.url).pathname;
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, readFileSync } from "node:fs";
const [out, query, clipsArg, ...rest] = process.argv.slice(2);
const tag = rest.includes("--tag") ? rest[rest.indexOf("--tag") + 1] : "";
const clips = clipsArg.split(",");
mkdirSync(out, { recursive: true });
const browser = await launch(["--autoplay-policy=no-user-gesture-required"]);
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on("pageerror", (e) => console.log("[pageerror]", e.message));
// push the scripted conversation far away so nothing else drives her
await page.route("**/demo/plan.json", async (r) => {
  const res = await r.fetch(); const j = await res.json(); j.start = 5000; await r.fulfill({ json: j });
});
const HIDE = ".instinct,.captions,.mouth,.pipeline,.honesty,.brand,.card-overlay,#title-card,.hud{display:none!important}";
const packs = {};
for (const id of new URLSearchParams(query).getAll("talk")) packs[id] = JSON.parse(readFileSync(`${ROOT}assets/clips/${id}/pack.json`, "utf8"));
for (const name of clips) {
  await page.goto(`${process.env.STAGE ?? "http://127.0.0.1:8080"}/stage/?capture=1&${query}`);
  await page.waitForSelector("body[data-ready='1']", { timeout: 90000 });
  await page.addStyleTag({ content: HIDE });
  const talkId = new URLSearchParams(query).get("talk");
  let idx = -1, energy = null;
  if (name.startsWith("talk_") && talkId && packs[talkId]) {
    const tc = Object.entries(packs[talkId].clips).filter(([, c]) => c.layer === "talk");
    const me = tc.find(([n]) => n === name);
    energy = me[1].energy; idx = tc.filter(([, c]) => c.energy === energy).findIndex(([n]) => n === name);
  }
  const dur = await page.evaluate(async ({ name, idx, energy }) => {
    const c = window.capture.character;
    for (let x = 0; x < 1.0; x += 1 / 30) await window.capture.frame(x);
    if (idx >= 0) {
      const s = c.talk.loops[energy][idx];
      c.talk.loops = { calm: [s], animated: [s], excited: [s] };
      c.talk.cur = null;
      const sm = c.setMouth.bind(c);
      c.setMouth = (w, sp) => sm(w, true);
      return s.duration * 2 + 0.6;
    }
    if (c.meta[name]?.loop) {
      if (c.idleName !== name) { c.idlePool = [name]; c.listenPool = []; c.idleSince = -1e9; }
      return c.meta[name].duration + 0.5;
    }
    c.playClip(name, { energy: "moderate" });
    return c.meta[name].duration + 0.8;
  }, { name, idx, energy });
  const tmp = `${out}/${name}${tag}.d`;
  rmSync(tmp, { recursive: true, force: true }); mkdirSync(tmp, { recursive: true });
  const n = Math.round(dur * 30);
  for (let i = 0; i < n; i++) {
    await page.evaluate((x) => window.capture.frame(x), 1.0 + i / 30);
    await page.screenshot({ path: `${tmp}/${String(i).padStart(4, "0")}.jpg`, type: "jpeg", quality: 88, clip: { x: 420, y: 60, width: 640, height: 1000 } });
  }
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", "30", "-i", `${tmp}/%04d.jpg`, "-vf", "scale=480:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", `${out}/${name}${tag}.mp4`]);
  const pick = Array.from({ length: 8 }, (_, k) => Math.min(n - 1, Math.round((k * (n - 1)) / 7)));
  const inputs = pick.flatMap((k) => ["-i", `${tmp}/${String(k).padStart(4, "0")}.jpg`]);
  const labels = pick.map((k, j) => `[${j}]scale=240:-2,drawtext=text='${(k / 30).toFixed(2)}s':x=6:y=6:fontsize=18:fontcolor=black[v${j}]`).join(";");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", ...inputs, "-filter_complex", `${labels};${pick.map((_, j) => `[v${j}]`).join("")}hstack=${pick.length}`, "-frames:v", "1", `${out}/${name}${tag}_sheet.jpg`]);
  rmSync(tmp, { recursive: true, force: true });
  console.log("done", name, n, "frames");
}
await browser.close();
