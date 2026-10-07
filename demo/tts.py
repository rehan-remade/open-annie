#!/usr/bin/env python3
"""Generate the demo voice lines for open-annie with Kokoro-82M.

Reads demo/script.json and writes:
  demo/audio/<id>.wav    24 kHz mono PCM16, trimmed (~40 ms padding), ~-16 LUFS
  demo/audio/lines.json  [{id, who, text, file, duration_s, words:[{word,start_s,end_s}], ...}]
For beats with "interrupted_at_s", also writes <id>_cut.wav (cut at the first
word boundary at/after that time, 60 ms fade-out) and an extra lines.json entry.

Word timings come from Kokoro's own duration predictor (KPipeline tokens
start_ts/end_ts), shifted to match the trimmed file. Re-runnable: overwrites
all outputs, except beats whose voice is "openai:..." (performed by
user_voice.py), whose committed takes are kept as they are.

Usage:  demo/.venv/bin/python demo/tts.py
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
import torch
from kokoro import KPipeline

from lines_util import SR, normalize, rnd, stats, trim_padded

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "script.json"
OUT = HERE / "audio"

CUT_FADE_S = 0.060
SPEED = {"annie": 1.0, "user": 1.05}
REPO_ID = "hexgrad/Kokoro-82M"
SEED = 0

PUNCT_RE = re.compile(r"^[^\w']+$")


def synth(pipe, text, voice, speed):
    """Return (audio float32, words[{word,start_s,end_s}], timing_kind)."""
    chunks, words, offset, estimated = [], [], 0.0, False
    for res in pipe(text, voice=voice, speed=speed):
        audio = res.audio.detach().cpu().numpy().astype(np.float32)
        for tok in res.tokens or []:
            t = tok.text
            if PUNCT_RE.match(t):
                # attach punctuation to the previous word so transcripts read naturally
                if words:
                    words[-1]["word"] += t
                continue
            s, e = tok.start_ts, tok.end_ts
            if s is None or e is None:
                estimated = True
            words.append({
                "word": t,
                "start_s": None if s is None else s + offset,
                "end_s": None if e is None else e + offset,
            })
        chunks.append(audio)
        offset += len(audio) / SR
    audio = np.concatenate(chunks)
    if estimated:
        _fill_missing(words, len(audio) / SR)
    return audio, words, ("estimated" if estimated else "kokoro")


def _fill_missing(words, dur):
    """Last-resort: distribute duration over characters for any untimed words."""
    total = sum(len(w["word"]) for w in words) or 1
    t = 0.0
    for w in words:
        d = dur * len(w["word"]) / total
        if w["start_s"] is None:
            w["start_s"] = t
        if w["end_s"] is None:
            w["end_s"] = w["start_s"] + d
        t = w["end_s"]












def main():
    cfg = json.loads(SCRIPT.read_text())
    voices = cfg["voices"]
    OUT.mkdir(exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipe = KPipeline(lang_code="a", repo_id=REPO_ID, device=device)
    meter = pyln.Meter(SR)  # BS.1770
    lines, problems = [], []
    old = {l["id"]: l for l in json.loads((OUT / "lines.json").read_text())} if (OUT / "lines.json").exists() else {}

    for beat in cfg["beats"]:
        bid, who, text = beat["id"], beat["who"], beat["text"]
        if voices[who].startswith("openai:"):
            # Performed by user_voice.py (not deterministic): keep its committed take.
            if bid in old and old[bid]["text"] == text:
                lines.append(old[bid])
            else:
                problems.append(f"{bid}: no take for this text yet; run user_voice.py")
            continue
        torch.manual_seed(SEED)  # Kokoro's vocoder injects noise; seed for reproducibility
        raw, words, timing = synth(pipe, text, voices[who], SPEED[who])

        audio = trim_padded(raw, words)
        dur = len(audio) / SR

        audio = normalize(audio, meter)
        fname = f"{bid}.wav"
        sf.write(OUT / fname, audio, SR, subtype="PCM_16")
        st = stats(audio, meter, len(words))
        entry = {"id": bid, "who": who, "text": text, "file": fname, **st,
                 "timing": timing, "words": rnd(words)}
        lines.append(entry)
        if not 2.0 <= st["words_per_s"] <= 4.0:
            problems.append(f"{bid}: {st['words_per_s']} words/s outside 2-4")
        if st["peak_dbfs"] > -0.5:
            problems.append(f"{bid}: peak {st['peak_dbfs']} dBFS")

        if "interrupted_at_s" in beat:
            t_int = float(beat["interrupted_at_s"])
            ends = [w["end_s"] for w in words if w["end_s"] >= t_int]
            cut_t = ends[0] if ends else dur
            n = int(round(cut_t * SR))
            cut = audio[:n].copy()
            f = min(int(CUT_FADE_S * SR), len(cut))
            cut[len(cut) - f:] *= np.linspace(1.0, 0.0, f, dtype=np.float32) ** 2
            cut_words = [w for w in words if w["end_s"] <= cut_t + 1e-6]
            cname = f"{bid}_cut.wav"
            sf.write(OUT / cname, cut, SR, subtype="PCM_16")
            cst = stats(cut, meter, len(cut_words))
            lines.append({"id": f"{bid}_cut", "who": who,
                          "text": " ".join(w["word"] for w in cut_words),
                          "file": cname, **cst, "timing": timing,
                          "cut_from": bid, "interrupted_at_s": t_int,
                          "cut_at_s": round(cut_t, 3), "fade_out_s": CUT_FADE_S,
                          "words": rnd(cut_words)})

    (OUT / "lines.json").write_text(json.dumps(lines, indent=2, ensure_ascii=False) + "\n")

    print(f"{'id':24} {'dur':>6} {'wps':>5} {'LUFS':>7} {'peak':>6}  timing")
    for e in lines:
        print(f"{e['id']:24} {e['duration_s']:6.2f} {e['words_per_s']:5.2f} "
              f"{e['lufs']:7.2f} {e['peak_dbfs']:6.2f}  {e['timing']}")
    for p in problems:
        print("WARN:", p, file=sys.stderr)


if __name__ == "__main__":
    main()
