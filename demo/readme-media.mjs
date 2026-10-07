// README media, from a framed demo video (frame.mjs):
//   docs/media/banner.png          banner.html at 2x: the title card's wordmark and the outro's pills
//   docs/media/open-annie-demo.mp4 the framed video, re-encoded smaller for the repo
//   docs/media/open-annie-demo.gif a short silent highlight that autoplays on GitHub (links to the mp4)
//   node readme-media.mjs [--in out/open-annie-live-v11-browser.mp4] [--gif START,SECONDS]
import { launch } from "./browser.mjs";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";

const args = process.argv.slice(2);
const opt = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
const HERE = new URL(".", import.meta.url).pathname;
const OUT = new URL("../docs/media/", import.meta.url).pathname;
const FFMPEG = process.env.FFMPEG ?? "ffmpeg";
const input = opt("--in", `${HERE}out/open-annie-live-v11-browser.mp4`);
const [gifAt, gifLen] = opt("--gif", "61.6,6.5").split(",");
const ff = (a) => execFileSync(FFMPEG, ["-nostdin", "-y", "-loglevel", "error", ...a], { timeout: 30 * 60_000, stdio: "inherit" });
mkdirSync(OUT, { recursive: true });

// The WSL GPU path fails to capture this static page ("Unable to capture screenshot");
// software rendering is plenty for one still.
process.env.ANNIE_GPU = "0";
const browser = await launch([]);
const page = await browser.newPage({ viewport: { width: 1280, height: 400 }, deviceScaleFactor: 2 });
page.on("pageerror", (e) => console.error("[banner.html]", e.message));
await page.goto(`file://${HERE}banner.html`);
await page.waitForSelector("body[data-ready='1']", { timeout: 30000 });
await page.screenshot({ path: `${OUT}banner.png` });
await browser.close();
console.log(`wrote ${OUT}banner.png`);

const dur = execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", input]).toString().trim();
ff(["-i", input, "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
  "-t", dur, "-movflags", "+faststart", `${OUT}open-annie-demo.mp4`]);
console.log(`wrote ${OUT}open-annie-demo.mp4`);

// The browser window only (frame.mjs layout: x 104, y 23, 1712 x 1035): the desktop gradient
// bands in a 256-colour GIF. One palette for the whole clip.
const vf = "fps=12,crop=1712:1035:104:23,scale=880:-1:flags=lanczos";
const palette = `${HERE}out/gif-palette.png`;
ff(["-ss", gifAt, "-t", gifLen, "-i", input, "-vf", `${vf},palettegen=stats_mode=diff:max_colors=224`, palette]);
ff(["-ss", gifAt, "-t", gifLen, "-i", input, "-i", palette, "-lavfi", `${vf}[x];[x][1:v]paletteuse=dither=sierra2_4a:diff_mode=rectangle`,
  `${OUT}open-annie-demo.gif`]);
console.log(`wrote ${OUT}open-annie-demo.gif`);
