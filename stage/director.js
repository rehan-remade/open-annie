// Director for recorded live sessions: plays scripted user lines into a synthetic mic
// at conversational moments (after Annie finishes, or while she is still talking),
// and records everything a frame-exact replay needs.

const RECORDED = ["user.partial", "user.final", "assistant.partial", "assistant.final", "assistant.audio.start", "assistant.audio.end", "bargein"];
const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000));

export async function direct({ bus, live, plan, lines, wall, ctx, dest, audioBase }) {
  const byId = Object.fromEntries(lines.map((l) => [l.id, l]));
  const buffers = {};
  for (const s of plan.steps) {
    if (s.say) buffers[s.say] = await ctx.decodeAudioData(await fetch(`${audioBase}/${byId[s.say].file}`).then((r) => r.arrayBuffer()));
  }
  // Session time zero sits 1.5 s before the first cue so the title card has room.
  const T0 = wall() - 1.5;
  const now = () => wall() - T0;
  const rec = { bus: [], userPlays: [] };
  const counts = {};
  for (const type of RECORDED) {
    bus.on(type, (detail) => {
      counts[type] = (counts[type] ?? 0) + 1;
      rec.bus.push({ t: now(), type, detail });
    });
  }

  const chunks = [];
  const mr = new MediaRecorder(live.remote, { mimeType: "audio/webm;codecs=opus" });
  mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const started = new Promise((r) => (mr.onstart = () => r(now())));
  mr.start(250);
  const audioStart = await started;

  const waitCount = async (type, mark, timeout) => {
    const until = now() + timeout;
    while ((counts[type] ?? 0) <= mark && now() < until) await sleep(0.05);
    return (counts[type] ?? 0) > mark;
  };

  // "Done" means she spoke after our line ended and has now been silent (audio and
  // transcript) for `quiet` seconds (the plan's "quiet", default 2.5): her transcript
  // alone arrives in bursts.
  // GPT-Live can also pause ~4.5 s mid-question ("How're... they doing?"), so an
  // unfinished sentence buys up to 8 s of silence before we speak again.
  let annieText = "";
  bus.on("assistant.partial", ({ text }) => (annieText = text));
  bus.on("assistant.final", ({ text }) => (annieText = text));
  const annieDone = async (since, quiet = 2.5, timeout = 30) => {
    const until = now() + timeout;
    while (now() < until) {
      const last = live.lastAnnieActive - T0;
      const finished = /[.!?…]["'”)]?$/.test(annieText.trim());
      if (last > since && now() - last > (finished ? quiet : 8)) return true;
      await sleep(0.05);
    }
    return false;
  };

  let mark = { ...counts };
  let lineAt = 0; // when our last line finished playing
  for (const step of plan.steps) {
    if (step.after === "annie_done") await annieDone(lineAt, plan.quiet ?? 2.5);
    if (step.after === "annie_speaking") await waitCount("assistant.audio.start", mark["assistant.audio.start"] ?? 0, 15);
    await sleep(step.gap ?? 0.8);
    if (step.end) break;
    mark = { ...counts };
    lineAt = now() + buffers[step.say].duration;
    const src = ctx.createBufferSource();
    src.buffer = buffers[step.say];
    src.connect(dest);
    src.start();
    const l = byId[step.say];
    rec.userPlays.push({ id: l.id, t: now(), file: l.file, duration_s: l.duration_s, words: l.words });
    console.log(`director: said ${l.id} at ${now().toFixed(2)} s`);
  }

  live.close();
  await sleep(0.3);
  const stopped = new Promise((r) => (mr.onstop = r));
  mr.stop();
  await stopped;
  const blob = new Blob(chunks, { type: "audio/webm" });
  const audioB64 = await new Promise((r) => {
    const fr = new FileReader();
    fr.onload = () => r(fr.result.split(",")[1]);
    fr.readAsDataURL(blob);
  });
  return { session: { duration: now() + 1.0, voice: plan.voice, audio: { start: audioStart }, ...rec }, audioB64, usageSeconds: live.usageSeconds };
}
