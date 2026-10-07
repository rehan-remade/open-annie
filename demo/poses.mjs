// Contact sheet: every clip at its peak, for eyeballing retarget/pose bugs.
import { launch } from "./browser.mjs";
const out = process.env.OUT ?? ".";
const browser = await launch([]);
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on("pageerror", (e) => console.log("[pageerror]", e.message));
await page.goto(`http://127.0.0.1:8080/stage/?capture=1${process.env.AVATAR ? "&avatar=" + process.env.AVATAR : ""}`);
await page.waitForSelector("body[data-ready='1']", { timeout: 60000 });
await page.addStyleTag({ content: ".instinct,.captions,.mouth,.pipeline,.honesty,.brand,.card-overlay{display:none!important}" });
const clips = (process.env.CLIPS ?? "idle_c,wave,excited_bounce,clap,dance,sad_slump,shrug,think,laugh,surprised_recoil,bow,point_self").split(",");
for (const name of clips) {
  const frac = Number(process.env.FRAC ?? 0.45);
  await page.evaluate(async ({ name, frac }) => {
    const c = window.capture.character;
    const t0 = 1.0;
    for (let x = 0; x < 0.5; x += 1 / 30) await window.capture.frame(t0 + x);
    c.playClip(name, { energy: "moderate" });
    const d = (c.clips[name].duration) * frac;
    for (let x = 0; x <= d; x += 1 / 30) await window.capture.frame(t0 + 0.5 + x);
  }, { name, frac });
  await page.screenshot({ path: `${out}/pose_${name}.png`, clip: { x: 360, y: 0, width: 760, height: 1080 } });
  await page.reload();
  await page.waitForSelector("body[data-ready='1']", { timeout: 60000 });
  await page.addStyleTag({ content: ".instinct,.captions,.mouth,.pipeline,.honesty,.brand,.card-overlay{display:none!important}" });
}
await browser.close();
