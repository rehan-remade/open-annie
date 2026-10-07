#!/usr/bin/env python3
"""Score mouth pipelines against phoneme ground truth.

Ground truth: Kokoro lines -> out/gt_kokoro.json (exact, Kokoro's duration predictor);
live take -> out/gt_ctc.json (CTC forced alignment). Predictions: out/providers.json
(providers.mjs; per-frame mouth weights at the stage's frame rate, the mouth displayed
at time t while audio t is heard).

Metrics
  vowel5    collapsed 5-vowel accuracy (A I U E O) on GT vowel frames: the displayed
            mouth must be open (>= 0.15) and its biggest vowel channel must match.
  open_vowel/open_cons  mean displayed aperture (x100) on GT vowel / consonant frames.
  shut_on_vowel  GT vowel frames shown closed (open < 0.10): the price of eager closures.
  sil       silence recall: GT silence frames (>= 60 ms from speech) with open < 0.10.
  closure   bilabial closure recall: GT p/b/m whose window (+-20 ms) reaches
            open <= max(0.10, 0.35 x local peak within +-200 ms).
  offset    A/V offset: lag maximising the correlation of displayed aperture with GT
            openness; positive = mouth leads the audio.
  cls15/cls5 raw HeadAudio frame accuracy (15 visemes / 5 vowels on vowel frames).

  .venv/bin/python score.py [providers.json] [--json]
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
VIS = ["aa", "E", "ih", "oh", "ou", "PP", "SS", "TH", "DD", "FF", "kk", "nn", "RR", "CH", "sil"]
CH5 = ["aa", "ih", "ou", "ee", "oh"]  # providers.json channel order
V5 = {"aa": "A", "ih": "I", "ou": "U", "E": "E", "oh": "O"}
CH5_CLASS = ["A", "I", "U", "E", "O"]
OPEN = {"aa": 1, "E": 0.8, "ih": 0.6, "oh": 0.85, "ou": 0.6, "PP": 0, "SS": 0.25, "TH": 0.3, "DD": 0.3, "FF": 0.1,
        "kk": 0.35, "nn": 0.25, "RR": 0.35, "CH": 0.3, "sil": 0}

# Phones (misaki for Kokoro, espeak IPA for CTC) -> visemes. Diphthongs split 60/40.
P2V = {}
for v, ps in {
    "aa": "a ɑ ɑː ɐ æ aː ä ɑ̃",
    "E": "ɛ e eː ɜ ɜː ʌ ə ɚ ᵊ ɘ œ ɝ əl ɚː",
    "ih": "i iː ɪ ɨ ᵻ y ʏ iə",
    "oh": "o oː ɔ ɔː ɒ ø",
    "ou": "u uː ʊ ɯ ɤ",
    "PP": "p b m",
    "SS": "s z ʃ ʒ h ç x ɣ ɕ ʑ",
    "TH": "θ ð",
    "DD": "t d ɾ ʔ",
    "FF": "f v",
    "kk": "k g ɡ q",
    "nn": "n ŋ ɲ n̩",
    "RR": "ɹ r l ɫ j w ɾ̃ ɚɹ ɑːɹ ɔːɹ oːɹ ɛɹ ɪɹ ʊɹ aɪɚ",
    "CH": "ʧ ʤ tʃ dʒ ts",
    "sil": ", . ! ? ; : — … \" ( )",
}.items():
    for p in ps.split():
        P2V[p] = [v]
DIPH = {"I": ["aa", "ih"], "A": ["E", "ih"], "W": ["aa", "ou"], "O": ["oh", "ou"], "Y": ["oh", "ih"],
        "aɪ": ["aa", "ih"], "eɪ": ["E", "ih"], "aʊ": ["aa", "ou"], "oʊ": ["oh", "ou"], "ɔɪ": ["oh", "ih"],
        "ɑːɹ": ["aa", "RR"], "ɔːɹ": ["oh", "RR"], "oːɹ": ["oh", "RR"], "ɛɹ": ["E", "RR"], "ɪɹ": ["ih", "RR"], "ʊɹ": ["ou", "RR"],
        "əl": ["E", "RR"], "iə": ["ih", "E"], "aɪɚ": ["aa", "E"]}


def gt_frames(phones, dur, hop, kokoro):
    """Label grid (hop seconds) of viseme names."""
    n = int(np.ceil(dur / hop)) + 1
    lab = np.array(["sil"] * n, dtype=object)
    ph = []
    pend = None  # Kokoro stress marks carry the onset of the following vowel
    for p, s, e in phones:
        if p in ("ˈ", "ˌ", "ː"):
            pend = s if pend is None else pend
            continue
        if pend is not None:
            s, pend = pend, None
        ph.append([p, s, e])
    if kokoro:  # word-gap spaces (dropped) up to 60 ms belong to the previous phone
        for i in range(len(ph) - 1):
            if 0 < ph[i + 1][1] - ph[i][2] <= 0.06 and P2V.get(ph[i][0]) != ["sil"]:
                ph[i][2] = ph[i + 1][1]
    unknown = set()
    for p, s, e in ph:
        vs = DIPH.get(p) or P2V.get(p)
        if vs is None:
            unknown.add(p)
            vs = ["DD"]
        cuts = [s, e] if len(vs) == 1 else [s, s + 0.6 * (e - s), e]
        for v, a, b in zip(vs, cuts, cuts[1:]):
            lab[int(round(a / hop)) : int(round(b / hop))] = v
    return lab, unknown


def energy_db(wav, hop):
    x, sr = sf.read(wav, dtype="float32", always_2d=True)
    x = x.mean(1)
    n = int(sr * hop)
    m = len(x) // n
    return 20 * np.log10(np.sqrt(np.mean(x[: m * n].reshape(m, n) ** 2, 1)) + 1e-9)


QUIET_DB = -50


def quiet_to_sil(lab, db, hop, min_s=0.06):
    """Runs of quiet audio (>= 60 ms) are silence, except bilabial closures (silent by nature)."""
    q = np.zeros(len(lab), bool)
    q[: min(len(lab), len(db))] = db[: len(lab)] < QUIET_DB
    k = int(min_s / hop)
    i = 0
    while i < len(q):
        if q[i]:
            j = i
            while j < len(q) and q[j]:
                j += 1
            if j - i >= k:
                seg = lab[i:j]
                seg[seg != "PP"] = "sil"
            i = j
        else:
            i += 1
    return lab


def vad_shift(labs_dbs, hop, maxlag=0.1):
    """Lag (s) to add to GT so its speech/silence matches the audio's (db > -40) best."""
    g = np.concatenate([(lab[: min(len(lab), len(db))] != "sil") for lab, db in labs_dbs]).astype(float)
    v = np.concatenate([(db[: min(len(lab), len(db))] > -40) for lab, db in labs_dbs]).astype(float)
    n, L = len(g), int(maxlag / hop)
    cc = [np.corrcoef(g[max(0, -k) : n - max(0, k)], v[max(0, k) : n - max(0, -k)])[0, 1] for k in range(-L, L + 1)]
    return (int(np.argmax(cc)) - L) * hop


def at(lab, hop, t):
    i = np.clip(np.round(t / hop).astype(int), 0, len(lab) - 1)
    return lab[i]


def lagcorr(x, y, fps, maxlag=0.25):
    """Lag (s) maximising corr(x(t), y(t + lag)); positive = x (mouth) leads y (GT)."""
    x = (x - x.mean()) / (x.std() + 1e-9)
    y = (y - y.mean()) / (y.std() + 1e-9)
    L = int(maxlag * fps)
    lags = np.arange(-L, L + 1)
    c = np.array([np.mean(x[max(0, -k) : len(x) - max(0, k)] * y[max(0, k) : len(y) - max(0, -k)]) for k in lags])
    i = int(c.argmax())
    frac = 0.0
    if 0 < i < len(c) - 1:  # parabolic refinement
        a, b, d = c[i - 1], c[i], c[i + 1]
        frac = 0.5 * (a - d) / (a - 2 * b + d + 1e-12)
    return (lags[i] + frac) / fps, float(c[i])


def score_clip(rec, fps, lab, hop, pipe):
    W = np.array([r[:5] for r in rec[pipe]], float)
    T = np.arange(len(W)) / fps
    g = at(lab, hop, T)
    opn = W.sum(1)
    cls = np.array(CH5_CLASS)[W.argmax(1)]
    res = {}
    vmask = np.isin(g, list(V5))
    gv = np.array([V5.get(x, "") for x in g])
    ok = (opn >= 0.15) & (cls == gv)
    res["vowel5"] = (ok[vmask].sum(), vmask.sum())
    res["vowel5_open"] = ((cls == gv)[vmask & (opn >= 0.15)].sum(), (vmask & (opn >= 0.15)).sum())
    res["shut_on_vowel"] = ((opn[vmask] < 0.10).sum(), vmask.sum())
    # mean aperture on vowels vs consonants (x1000 so it sums like the counters)
    cmask = ~vmask & (g != "sil")
    res["open_vowel"] = (1000 * opn[vmask].sum() / 1000, vmask.sum())
    res["open_cons"] = (1000 * opn[cmask].sum() / 1000, cmask.sum())
    # silence at least 60 ms from any speech label
    sil = g == "sil"
    k = int(round(0.06 * fps))
    far = sil.copy()
    for d in range(1, k + 1):
        far[d:] &= sil[:-d]
        far[:-d] &= sil[d:]
    res["sil"] = ((opn[far] < 0.10).sum(), far.sum())
    # bilabial closures
    hits = tot = 0
    i = 0
    while i < len(g):
        if g[i] == "PP":
            j = i
            while j < len(g) and g[j] == "PP":
                j += 1
            a, b = max(0, i - int(0.02 * fps)), min(len(g), j + int(0.02 * fps))
            pa, pb = max(0, i - int(0.2 * fps)), min(len(g), j + int(0.2 * fps))
            peak = opn[pa:pb].max()
            hits += opn[a:b].min() <= max(0.10, 0.35 * peak)
            tot += 1
            i = j
        else:
            i += 1
    res["closure"] = (hits, tot)
    gopen = np.array([OPEN[x] for x in g])
    res["xc"] = (opn, gopen)
    return res


def score_classifier(rec, lab, hop):
    ok15 = n15 = ok5 = n5 = ok5v = 0
    for t, vote, p in rec["haFrames"]:
        gl = at(lab, hop, np.array([t]))[0]
        if p is None or gl == "sil":
            continue
        pv = VIS[int(np.argmax(p))]
        n15 += 1
        ok15 += pv == gl
        if gl in V5:
            n5 += 1
            # best vowel among the 5 vowel probabilities
            ok5 += VIS[int(np.argmax(p[:5]))] == gl
            ok5v += VIS[vote] == gl
    return (ok15, n15), (ok5, n5), (ok5v, n5)


def main():
    pth = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else HERE / "out/providers.json"
    prov = json.loads(pth.read_text())
    fps = prov["fps"]
    gts = {}
    gk = json.loads((HERE / "out/gt_kokoro.json").read_text())
    gc = json.loads((HERE / "out/gt_ctc.json").read_text()) if (HERE / "out/gt_ctc.json").exists() else {}
    hop = 0.005
    unknown = set()
    clips = {c["id"]: c for c in json.loads((HERE / "clips.json").read_text())}
    # Kokoro's decoder runs ~2 frames ahead of its cumulative duration grid (its own
    # join_timestamps subtracts 3): measure the shift against the audio and apply it.
    pairs = [(gt_frames(gk[c]["phones"], gk[c]["dur"], hop, True)[0], energy_db(HERE / clips[c]["wav"], hop)) for c in gk if c in prov["clips"]]
    ks = vad_shift(pairs, hop) if pairs else 0.0
    print(f"kokoro GT shift {1000 * ks:+.0f} ms (speech/silence correlation vs audio)", file=sys.stderr)
    for cid in prov["clips"]:
        db = energy_db(HERE / clips[cid]["wav"], hop)
        if cid in gk and "--ctc" not in sys.argv:
            ph = [[p, s + ks, e + ks] for p, s, e in gk[cid]["phones"]]
            lab, u = gt_frames(ph, gk[cid]["dur"], hop, True)
        elif cid in gc:
            lab, u = gt_frames(gc[cid]["phones"], gc[cid]["dur"], hop, False)
        else:
            continue
        unknown |= u
        gts[cid] = quiet_to_sil(lab, db, hop)
    if unknown:
        print("unmapped phones (as DD):", " ".join(sorted(unknown)), file=sys.stderr)

    groups = {"kokoro": [c for c in gts if c != "live"], "live": [c for c in gts if c == "live"]}
    pipes = [p for p in ("before", "energy", "ha") if p in next(iter(prov["clips"].values()))]
    table = {}
    for gname, cids in groups.items():
        if not cids:
            continue
        for pipe in pipes:
            acc = {}
            xs, ys = [], []
            for cid in cids:
                r = score_clip(prov["clips"][cid], fps, gts[cid], hop, pipe)
                for k, v in r.items():
                    if k == "xc":
                        xs.append(v[0]); ys.append(v[1])
                    else:
                        a = acc.setdefault(k, [0, 0]); a[0] += v[0]; a[1] += v[1]
            lag, c = lagcorr(np.concatenate(xs), np.concatenate(ys), fps)
            row = {k: round(100 * a[0] / max(1, a[1]), 1) for k, a in acc.items()}
            row["n_vowel"] = acc["vowel5"][1]; row["n_closure"] = acc["closure"][1]
            row["offset_ms"] = round(1000 * lag); row["xcorr"] = round(c, 3)
            table[f"{gname}/{pipe}"] = row
        c15 = [0, 0]; c5 = [0, 0]; c5v = [0, 0]
        for cid in cids:
            a, b, d = score_classifier(prov["clips"][cid], gts[cid], hop)
            c15[0] += a[0]; c15[1] += a[1]; c5[0] += b[0]; c5[1] += b[1]; c5v[0] += d[0]; c5v[1] += d[1]
        table[f"{gname}/headaudio-frames"] = {"cls15": round(100 * c15[0] / max(1, c15[1]), 1), "cls5_soft": round(100 * c5[0] / max(1, c5[1]), 1),
                                              "cls5_vote": round(100 * c5v[0] / max(1, c5v[1]), 1), "chance5": 20.0}
    if "--gtcheck" in sys.argv and gc:
        # CTC ground truth vs Kokoro's exact timings on the same lines: how good is the method?
        same = n = 0
        xo, yo = [], []
        for cid in groups["kokoro"]:
            db = energy_db(HERE / clips[cid]["wav"], hop)
            a = gts[cid]
            b = quiet_to_sil(gt_frames(gc[cid]["phones"], gc[cid]["dur"], hop, False)[0], db, hop)
            m = min(len(a), len(b))
            coarse = lambda x: np.array([V5.get(v, "closed" if v in ("PP", "sil") else "cons") for v in x])
            ca, cb = coarse(a[:m]), coarse(b[:m])
            speech = (a[:m] != "sil") | (b[:m] != "sil")
            same += (ca == cb)[speech].sum(); n += speech.sum()
            xo.append(np.array([OPEN[v] for v in b[:m]])); yo.append(np.array([OPEN[v] for v in a[:m]]))
        lag, c = lagcorr(np.concatenate(xo), np.concatenate(yo), 1 / hop, 0.15)
        print(f"GT check (CTC vs Kokoro, speech frames): coarse-class agreement {100 * same / n:.1f}%, "
              f"openness lag {1000 * lag:+.0f} ms (+ = CTC early), r={c:.2f}")
    if "--json" in sys.argv:
        print(json.dumps(table, default=float))
        return
    for k, row in table.items():
        print(f"{k:26} " + "  ".join(f"{a}={b}" for a, b in row.items()))


if __name__ == "__main__":
    main()
