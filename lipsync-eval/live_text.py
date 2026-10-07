#!/usr/bin/env python3
"""Annie's turns (and the Kokoro lines) in the recorded live take, with espeak phonemes for forced alignment.

Turns come from the session bus (assistant.audio.start .. final), in annie.wav time
(session time - audio.start). Run with the demo venv (phonemizer + espeakng_loader):
  ../demo/.venv/bin/python live_text.py  ->  out/live_turns.json
"""
import json
from pathlib import Path
import espeakng_loader
from phonemizer.backend.espeak.wrapper import EspeakWrapper
from phonemizer import phonemize
from phonemizer.separator import Separator

EspeakWrapper.set_library(espeakng_loader.get_library_path())
EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
HERE = Path(__file__).resolve().parent
s = json.loads((HERE.parent / "demo/live/session.json").read_text())
a0 = s["audio"]["start"]
turns, cur = [], None
for e in sorted(s["bus"], key=lambda e: e["t"]):
    if e["type"] == "assistant.audio.start":
        cur = {"start": e["t"] - a0}
    elif e["type"] == "assistant.final" and cur:
        cur["end"] = e["t"] - a0
        cur["text"] = e["detail"]["text"]
        turns.append(cur)
        cur = None
for t in turns:
    t["phones"] = phonemize(t["text"], language="en-us", backend="espeak", separator=Separator(phone=" ", word=" | "), strip=True, with_stress=False, preserve_punctuation=False)
    print(f"{t['start']:6.2f}-{t['end']:6.2f} {t['text'][:50]!r}\n        {t['phones'][:80]}")
(HERE / "out/live_turns.json").write_text(json.dumps(turns, ensure_ascii=False, indent=1))
# The Kokoro lines too, so the CTC ground truth can be checked against Kokoro's exact timings.
lines = json.loads((HERE.parent / "demo/audio/lines.json").read_text())
kok = {l["id"]: phonemize(l["text"], language="en-us", backend="espeak", separator=Separator(phone=" ", word=" | "), strip=True, with_stress=False, preserve_punctuation=False)
       for l in lines if l["who"] == "annie" and not l["id"].endswith("_cut")}
(HERE / "out/kokoro_espeak.json").write_text(json.dumps(kok, ensure_ascii=False, indent=1))
