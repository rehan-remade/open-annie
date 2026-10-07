// Wraps a rendered demo video in a browser window (frame.html): the stage plays under a tab
// strip and an address bar showing localhost, on the brand background, so the video reads as
// what it is, the stage running in a browser.
//   node frame.mjs --in out/open-annie-live-v11.mp4 [--out out/x.mp4] [--url "localhost:8080/stage/?live=1"]
import { launch } from "./browser.mjs";
import { execFileSync } from "node:child_process";

const args = process.argv.slice(2);
const opt = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
const HERE = new URL(".", import.meta.url).pathname;
const FFMPEG = process.env.FFMPEG ?? "ffmpeg";
const input = opt("--in");
if (!input) throw new Error("usage: node frame.mjs --in video.mp4 [--out framed.mp4] [--url localhost:8080/stage/]");
const output = opt("--out", input.replace(/\.mp4$/, "-browser.mp4"));
const url = opt("--url", "localhost:8080/stage/?live=1");

// 1920x1080 canvas: the page keeps the video's 16:9 at 89% under a 72 px browser header.
const L = { cw: 1920, ch: 1080, w: 1712, h: 963, head: 72 };
L.x = (L.cw - L.w) / 2;
L.y = Math.round((L.ch - L.h - L.head) / 2) + L.head;

// The overlay is drawn at 2x and scaled down in ffmpeg, for smooth text and corners.
const overlay = `${HERE}out/frame-overlay.png`;
const browser = await launch([]);
const page = await browser.newPage({ viewport: { width: L.cw, height: L.ch }, deviceScaleFactor: 2 });
page.on("pageerror", (e) => console.error("[frame.html]", e.message));
const q = new URLSearchParams({ ...Object.fromEntries(Object.entries(L).map(([k, v]) => [k, String(v)])), url });
await page.goto(`file://${HERE}frame.html?${q}`);
await page.waitForSelector("body[data-ready='1']", { timeout: 30000 });
await page.screenshot({ path: overlay, omitBackground: true });
await browser.close();

const dur = execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", input]).toString().trim();
const graph = [
  `color=c=0xf4f3f1:s=${L.cw}x${L.ch}:r=30:d=${dur}[bg]`,
  `[0:v]scale=${L.w}:${L.h}:flags=lanczos,setsar=1[pg]`,
  `[1:v]scale=${L.cw}:${L.ch}:flags=lanczos,format=rgba[ov]`,
  `[bg][pg]overlay=${L.x}:${L.y}:shortest=1[b]`,
  `[b][ov]overlay=0:0:shortest=1,format=yuv420p[v]`,
].join(";");
execFileSync(FFMPEG, [
  // Explicit -t, never -shortest alone: this ffmpeg build can hang waiting on a looped input.
  "-nostdin", "-y", "-loglevel", "error", "-i", input, "-loop", "1", "-framerate", "30", "-i", overlay,
  "-filter_complex", graph, "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
  "-c:a", "copy", "-t", dur, "-movflags", "+faststart", output,
], { timeout: 30 * 60_000, stdio: "inherit" });
console.log(`wrote ${output}`);
