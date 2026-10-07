// Quick look: render the stage at given virtual times and save screenshots.
import { launch } from "./browser.mjs";
const [, , url = "http://127.0.0.1:8080/stage/?capture=1", ...times] = process.argv;
const browser = await launch(["--autoplay-policy=no-user-gesture-required"]);
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on("console", (m) => console.log("[page]", m.type(), m.text()));
page.on("pageerror", (e) => console.log("[pageerror]", e.message));
await page.goto(url);
await page.waitForSelector("body[data-ready='1']", { timeout: 60000 });
const out = process.env.OUT ?? ".";
let prev = 0;
for (const ts of times.length ? times : ["0.5"]) {
  const t = Number(ts);
  // step at 30 fps up to t so animation state is realistic
  await page.evaluate(async ({ from, to }) => { for (let x = from; x <= to + 1e-6; x += 1 / 30) await window.capture.frame(x); }, { from: prev, to: t });
  prev = t + 1 / 30;
  await page.screenshot({ path: `${out}/shot_${ts}.png` });
  console.log("saved", ts);
}
await browser.close();
