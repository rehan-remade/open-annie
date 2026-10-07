#!/usr/bin/env python3
"""Phoneme ground truth by CTC forced alignment (wav2vec2 espeak phoneme recogniser).

facebook/wav2vec2-lv-60-espeak-cv-ft emits espeak IPA phones every 20 ms. We Viterbi-align
the known transcript (espeak-phonemised by live_text.py) to its emissions:
  - the live GPT-Live take (demo/live/annie.wav), one window per Annie turn;
  - the Kokoro lines too, to measure this method against Kokoro's exact timings.
CTC paths are spiky, so each phone owns the frames from its first frame to the next
phone's first frame; blank runs over 120 ms that are also quiet become silence.

  .venv/bin/python ctc_gt.py  ->  out/gt_ctc.json   (fp16 on GPU, ~1.3 GB VRAM)
"""
import json
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC
from huggingface_hub import hf_hub_download

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODEL = "facebook/wav2vec2-lv-60-espeak-cv-ft"
HOP = 0.02

dev = "cuda" if torch.cuda.is_available() else "cpu"
fe = Wav2Vec2FeatureExtractor.from_pretrained(MODEL)
model = Wav2Vec2ForCTC.from_pretrained(MODEL, torch_dtype=torch.float16 if dev == "cuda" else torch.float32).to(dev).eval()
vocab = json.loads(Path(hf_hub_download(MODEL, "vocab.json")).read_text())
BLANK = vocab.get("<pad>", 0)


def load16(path):
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(1)
    if sr != 16000:
        g = np.gcd(sr, 16000)
        x = resample_poly(x, 16000 // g, sr // g).astype(np.float32)
    return x


@torch.no_grad()
def emissions(x):
    inp = fe(x, sampling_rate=16000, return_tensors="pt").input_values.to(dev, model.dtype)
    return torch.log_softmax(model(inp).logits.float(), -1)[0].cpu().numpy()


def tokens(phones):
    out = []
    for p in phones.replace("|", " ").split():
        if p in vocab:
            out.append(p)
        else:  # split unknown multi-char phones into known pieces
            i = 0
            while i < len(p):
                for n in (3, 2, 1):
                    if p[i : i + n] in vocab:
                        out.append(p[i : i + n])
                        i += n
                        break
                else:
                    i += 1
    return out


def viterbi(em, ids):
    """CTC forced alignment. Returns, per frame, the index of the token (or -1 for blank)."""
    T, L = len(em), len(ids)
    S = 2 * L + 1
    lab = np.full(S, BLANK)
    lab[1::2] = ids
    NEG = -1e30
    dp = np.full((T, S), NEG)
    bp = np.zeros((T, S), np.int8)
    dp[0, 0] = em[0, lab[0]]
    if S > 1:
        dp[0, 1] = em[0, lab[1]]
    can_skip = np.zeros(S, bool)
    can_skip[3::2] = lab[3::2] != lab[1:-2:2]
    for t in range(1, T):
        prev = dp[t - 1]
        c0 = prev
        c1 = np.concatenate([[NEG], prev[:-1]])
        c2 = np.where(can_skip, np.concatenate([[NEG, NEG], prev[:-2]]), NEG)
        stack = np.stack([c0, c1, c2])
        k = stack.argmax(0)
        dp[t] = stack[k, np.arange(S)] + em[t, lab]
        bp[t] = k
    s = S - 1 if S == 1 or dp[-1, S - 1] >= dp[-1, S - 2] else S - 2
    path = np.zeros(T, int)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(bp[t, s])
    return np.where(path % 2 == 1, path // 2, -1), dp[-1].max()


def segments(x, em, toks, t0=0.0):
    ids = [vocab[p] for p in toks]
    tokf, _ = viterbi(em, ids)
    T = len(em)
    first = {}
    for f in range(T):
        if tokf[f] >= 0 and tokf[f] not in first:
            first[tokf[f]] = f
    starts = [first.get(i) for i in range(len(toks))]
    # frame energy (dBFS) on the 20 ms grid
    n = int(16000 * HOP)
    rms = np.array([np.sqrt(np.mean(x[i * n : (i + 1) * n] ** 2) + 1e-12) for i in range(T)])
    db = 20 * np.log10(rms)
    segs = []
    for i, p in enumerate(toks):
        s = starts[i]
        if s is None:
            continue
        nxt = next((starts[j] for j in range(i + 1, len(toks)) if starts[j] is not None), None)
        last_tok = max((f for f in range(s, T if nxt is None else nxt) if tokf[f] == i), default=s)
        e = nxt if nxt is not None else min(T, last_tok + 6)
        # Long trailing blank that is quiet: phone ends 60 ms after its last frame, rest is silence.
        tail = e - last_tok - 1
        if tail > 6 and np.median(db[last_tok + 1 : e]) < -45:
            e = last_tok + 1 + 3
        segs.append([p, round(t0 + s * HOP, 3), round(t0 + e * HOP, 3)])
    return segs


out = {}
turns = json.loads((HERE / "out/live_turns.json").read_text())
live = load16(ROOT / "demo/live/annie.wav")
live_segs = []
for k, tr in enumerate(turns):
    a = max(0.0, tr["start"] - 0.3)
    b = min(len(live) / 16000, tr["end"] + 0.6, turns[k + 1]["start"] - 0.1 if k + 1 < len(turns) else 1e9)
    x = live[int(a * 16000) : int(b * 16000)]
    toks = tokens(tr["phones"])
    em = emissions(x)
    segs = segments(x, em, toks, t0=a)
    print(f"live turn {k}: {a:6.2f}-{b:6.2f}s {len(toks)} phones, aligned {len(segs)}")
    live_segs += segs
out["live"] = {"phones": live_segs, "dur": len(live) / 16000, "method": "ctc"}

kok = json.loads((HERE / "out/kokoro_espeak.json").read_text())
for cid, ph in kok.items():
    x = load16(ROOT / f"demo/audio/{cid}.wav")
    segs = segments(x, emissions(x), tokens(ph))
    out[cid] = {"phones": segs, "dur": len(x) / 16000, "method": "ctc"}
    print(f"{cid}: {len(segs)} phones")
(HERE / "out/gt_ctc.json").write_text(json.dumps(out, ensure_ascii=False))
print("vram peak MiB", torch.cuda.max_memory_allocated() // 2**20 if dev == "cuda" else 0)
