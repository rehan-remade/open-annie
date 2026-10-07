// Deterministic demo capture.
//   node capture.mjs record [--broker http://127.0.0.1:8787]   live Jev → decisions.json
//   node capture.mjs render [--fps 30]                          frames from decisions.json
//   node capture.mjs mux [--name out.mp4]                       audio mix + mp4
//   node capture.mjs live [--broker ...]                        a real GPT-Live session, recorded → live/
//   node capture.mjs render --query "replay=demo/live/session.json&decisions=demo/live/decisions.json"
// The page runs on a virtual clock; decisions land at ask time + their measured latency.
import { launch } from "./browser.mjs";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync, readFileSync, rmSync, existsSync } from "node:fs";

const args = process.argv.slice(2);
const mode = args[0] ?? "render";
const opt = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
const STAGE = opt("--stage", "http://127.0.0.1:8080/stage/");
const FPS = Number(opt("--fps", 30));
const HERE = new URL(".", import.meta.url).pathname;
const FFMPEG = process.env.FFMPEG ?? "ffmpeg";
const ROOT = new URL("..", import.meta.url).pathname;

// SSAA=2 (default) renders at 2x device pixels and screenshots at CSS size, so every
// frame is supersampled (crisper hair, outlines and text) at the same 1080p output.
const SSAA = Number(process.env.SSAA ?? 2);
async function open(query) {
  const browser = await launch([]);
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: SSAA });
  page.on("pageerror", (e) => console.error("[pageerror]", e.message));
  page.on("console", (m) => m.type() === "error" && console.error("[page]", m.text()));
  await page.goto(`${STAGE}?capture=1&${query}`);
  await page.waitForSelector("body[data-ready='1']", { timeout: 120000 });
  await page.evaluate(() => document.fonts.ready);
  return { browser, page };
}

if (mode === "record") {
  const broker = opt("--broker", "http://127.0.0.1:8787");
  const { browser, page } = await open(`broker=${encodeURIComponent(broker)}`);
  const duration = await page.evaluate(() => window.capture.duration);
  const t0 = Date.now();
  await page.evaluate(async ({ duration, fps }) => {
    for (let i = 0; i * (1 / fps) <= duration; i++) await window.capture.frame(i / fps);
  }, { duration, fps: FPS });
  const log = await page.evaluate(() => window.capture.log());
  const events = await page.evaluate(() => window.capture.events());
  writeFileSync(`${HERE}decisions.json`, JSON.stringify(log, null, 1));
  writeFileSync(`${HERE}events.json`, JSON.stringify(events, null, 1));
  const lat = log.filter((d) => d.source === "jev").map((d) => d.latency_ms).sort((a, b) => a - b);
  console.log(`recorded ${log.length} decisions (${lat.length} live jev) in ${((Date.now() - t0) / 1000).toFixed(1)} s; p50 ${lat[lat.length >> 1]?.toFixed(0)} ms, p90 ${lat[Math.floor(lat.length * 0.9)]?.toFixed(0)} ms`);
  await browser.close();
} else if (mode === "render") {
  const dir = `${HERE}frames`;
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const query = opt("--query", existsSync(`${HERE}decisions.json`) ? "decisions=demo/decisions.json" : "");
  const { browser, page } = await open(query);
  const duration = await page.evaluate(() => window.capture.duration);
  const n = Math.ceil(duration * FPS);
  const t0 = Date.now();
  for (let i = 0; i < n; i++) {
    await page.evaluate((t) => window.capture.frame(t), i / FPS);
    await page.screenshot({ path: `${dir}/${String(i).padStart(5, "0")}.jpg`, type: "jpeg", quality: 93, scale: "css" });
    if (i % 150 === 0) console.log(`frame ${i}/${n}  ${((Date.now() - t0) / 1000).toFixed(0)} s`);
  }
  writeFileSync(`${HERE}events.json`, JSON.stringify(await page.evaluate(() => window.capture.events()), null, 1));
  const [beats, audio] = await page.evaluate(() => [window.capture.beats, window.capture.audio]);
  writeFileSync(`${HERE}timeline.json`, JSON.stringify({ duration, fps: FPS, beats, audio }, null, 1));
  console.log(JSON.stringify(await page.evaluate(() => window.capture.summary())));
  await browser.close();
} else if (mode === "mux") {
  const tl = JSON.parse(readFileSync(`${HERE}timeline.json`));
  const events = JSON.parse(readFileSync(`${HERE}events.json`));
  mkdirSync(`${HERE}out`, { recursive: true });
  // Voices placed on the timeline, plus the dance beat where the dance clip actually started.
  const inputs = [];
  const filters = [];
  tl.audio.forEach((a, i) => {
    inputs.push("-i", `${ROOT}${a.file}`);
    filters.push(`[${i}]aresample=44100,aformat=channel_layouts=mono,adelay=${Math.round(a.start * 1000)}:all=1[v${i}]`);
  });
  const dance = events.find((e) => e.type === "clip" && e.name === "dance");
  let mixIn = tl.audio.map((_, i) => `[v${i}]`).join("");
  let count = tl.audio.length;
  if (dance) {
    execFileSync(existsSync(`${HERE}.venv/bin/python`) ? `${HERE}.venv/bin/python` : "python3", [`${HERE}beat.py`, `${HERE}out/beat.wav`, String(6.2 / dance.rate), String(dance.rate)]);
    inputs.push("-i", `${HERE}out/beat.wav`);
    filters.push(`[${count}]aresample=44100,aformat=channel_layouts=mono,adelay=${Math.round(dance.t * 1000)}:all=1,volume=0.5[m]`);
    mixIn += "[m]";
    count++;
  }
  // apad → atrim never terminates in this ffmpeg build; an output -t does.
  filters.push(`${mixIn}amix=inputs=${count}:normalize=0:duration=longest,apad[a]`);
  execFileSync(FFMPEG, ["-nostdin", "-y", "-loglevel", "error", ...inputs, "-filter_complex", filters.join(";"), "-map", "[a]", "-t", tl.duration.toFixed(3), "-ac", "2", "-ar", "44100", `${HERE}out/audio.wav`]);
  execFileSync(FFMPEG, [
    // Explicit -t instead of -shortest, and stereo 44.1 kHz: this ffmpeg build's AAC encoder hangs on the 48 kHz mono mix.
    "-nostdin", "-y", "-loglevel", "error", "-framerate", String(tl.fps), "-i", `${HERE}frames/%05d.jpg`, "-i", `${HERE}out/audio.wav`,
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", tl.duration.toFixed(3), "-movflags", "+faststart",
    `${HERE}out/${opt("--name", "open-annie-demo.mp4")}`,
  ]);
  console.log(`wrote ${HERE}out/${opt("--name", "open-annie-demo.mp4")}`);
} else if (mode === "live") {
  // Real time, real GPT-Live: the director feeds user lines into a synthetic mic.
  const broker = opt("--broker", "http://127.0.0.1:8787");
  const browser = await launch(["--autoplay-policy=no-user-gesture-required"]);
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on("pageerror", (e) => console.error("[pageerror]", e.message));
  page.on("console", (m) => console.log("[page]", m.text()));
  await page.goto(`${STAGE}?live=1&broker=${encodeURIComponent(broker)}&direct=demo/live-plan.json`);
  await page.waitForFunction(() => window.liveRecord?.done, null, { timeout: 10 * 60_000, polling: 500 });
  const out = await page.evaluate(() => window.liveRecord.export());
  await browser.close();
  if (out.error) throw new Error(out.error);
  mkdirSync(`${HERE}live`, { recursive: true });
  writeFileSync(`${HERE}live/annie.webm`, Buffer.from(out.audioB64, "base64"));
  // GPT-Live's track lands ~10 LU under the -16 LUFS user lines; level it to match.
  execFileSync(FFMPEG, ["-nostdin", "-y", "-loglevel", "error", "-i", `${HERE}live/annie.webm`, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ac", "1", "-ar", "48000", `${HERE}live/annie.wav`]);
  writeFileSync(`${HERE}live/session.json`, JSON.stringify({ ...out.session, audio: { file: "demo/live/annie.wav", start: out.session.audio.start } }, null, 1));
  writeFileSync(`${HERE}live/decisions.json`, JSON.stringify(out.decisions, null, 1));
  const lat = out.decisions.filter((d) => d.source === "jev").map((d) => d.latency_ms).sort((a, b) => a - b);
  console.log(`live session ${out.session.duration.toFixed(1)} s, ${out.decisions.length} decisions (${lat.length} jev, p50 ${lat[lat.length >> 1]?.toFixed(0)} ms), voice seconds ${out.usageSeconds}`);
}
