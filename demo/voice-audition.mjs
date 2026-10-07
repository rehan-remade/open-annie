// node voice-audition.mjs marin,coral,...  → out/voices/<voice>.wav + transcript
import { launch } from "./browser.mjs";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
const voices = (process.argv[2] ?? "marin,coral,shimmer,sage,gleam,quartz,willow,delta,alloy").split(",");
const HERE = new URL(".", import.meta.url).pathname;
mkdirSync(`${HERE}out/voices`, { recursive: true });
const browser = await launch(["--autoplay-policy=no-user-gesture-required"]);
for (const voice of voices) {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:8080/demo/voice-audition.html?broker=${process.env.BROKER ?? "http://127.0.0.1:8790"}&voice=${voice}&line=01_user_blink.wav`);
  await page.waitForFunction(() => window.aud?.done, null, { timeout: 60000, polling: 250 });
  const r = await page.evaluate(() => window.aud);
  await page.close();
  if (r.error) { console.log(voice, "ERROR", r.error); continue; }
  writeFileSync(`${HERE}out/voices/${voice}.webm`, Buffer.from(r.b64, "base64"));
  execFileSync("ffmpeg", ["-nostdin", "-y", "-loglevel", "error", "-i", `${HERE}out/voices/${voice}.webm`, "-af", "silenceremove=start_periods=1:start_threshold=-45dB,loudnorm=I=-16", "-ac", "1", "-ar", "24000", `${HERE}out/voices/${voice}.wav`]);
  writeFileSync(`${HERE}out/voices/${voice}.txt`, r.text);
  console.log(voice, "→", r.text);
}
await browser.close();
