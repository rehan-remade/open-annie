#!/usr/bin/env python3
"""Exact phoneme timings for the Kokoro demo lines, from Kokoro's own duration predictor.

Re-synthesises each Annie line exactly as demo/tts.py did (same voice, speed, seed, trim)
and records every input phoneme with its predicted duration (1 frame = 600 samples @ 24 kHz).
Checks the re-synthesis against the shipped wav (correlation) so the timings are known to
belong to that audio.

Run with the demo venv (it has kokoro + misaki):
  ../demo/.venv/bin/python kokoro_gt.py   ->  out/gt_kokoro.json
"""
import json, sys
from pathlib import Path
import numpy as np, soundfile as sf, torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "demo"))
import tts  # noqa: E402
from kokoro import KPipeline  # noqa: E402

lines = json.loads((HERE.parent / "demo/audio/lines.json").read_text())
cfg = json.loads((HERE.parent / "demo/script.json").read_text())
pipe = KPipeline(lang_code="a", repo_id=tts.REPO_ID, device="cpu")
vocab = pipe.model.vocab
out = {}
for beat in cfg["beats"]:
    if beat["who"] != "annie":
        continue
    torch.manual_seed(tts.SEED)
    chunks, phones, offset = [], [], 0.0
    for res in pipe(beat["text"], voice=cfg["voices"]["annie"], speed=tts.SPEED["annie"]):
        audio = res.audio.detach().cpu().numpy().astype(np.float32)
        ids = [p for p in res.phonemes if vocab.get(p) is not None]
        dur = res.pred_dur.tolist()
        assert len(dur) == len(ids) + 2, (len(dur), len(ids))
        t = offset + dur[0] * 0.025
        for p, d in zip(ids, dur[1:-1]):
            phones.append([p, round(t, 4), round(t + d * 0.025, 4)])
            t += d * 0.025
        chunks.append(audio)
        offset += len(audio) / tts.SR
    raw = np.concatenate(chunks)
    s0, s1 = tts.trim_bounds(raw)
    pad = int(tts.PAD_S * tts.SR)
    a = max(0, s0 - pad)
    head = pad - (s0 - a)
    shift = (a - head) / tts.SR
    wav, sr = sf.read(HERE.parent / "demo/audio" / f"{beat['id']}.wav", dtype="float32")
    ref = np.concatenate([np.zeros(head, np.float32), raw[a:]])[: len(wav)]
    corr = float(np.corrcoef(ref[: len(wav)], wav[: len(ref)])[0, 1]) if len(ref) == len(wav) else float("nan")
    ph = [[p, round(s - shift, 4), round(e - shift, 4)] for p, s, e in phones if p.strip()]
    out[beat["id"]] = {"phones": ph, "shift": shift, "resynth_corr": corr, "dur": len(wav) / sr}
    print(f"{beat['id']:20} phones={len(ph):3d} shift={shift:.3f}s corr={corr:.4f}")
(HERE / "out").mkdir(exist_ok=True)
(HERE / "out/gt_kokoro.json").write_text(json.dumps(out, ensure_ascii=False, indent=0))
