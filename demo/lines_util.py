"""Shared post-processing for the demo voice lines (tts.py, user_voice.py): trim to
PAD_S of silence, -16 LUFS with a -1 dBFS lookahead limiter, lines.json stats."""
import math

import numpy as np

SR = 24000
PAD_S = 0.040          # silence kept at head/tail after trimming
TARGET_LUFS = -16.0
PEAK_CEIL_DB = -1.0    # soft ceiling for sample peaks after normalization


def trim_bounds(audio, thresh_db=-45.0, frame=240):
    """Sample indices of first/last frame whose RMS exceeds thresh (rel. to peak)."""
    peak = np.max(np.abs(audio)) + 1e-9
    n = len(audio) // frame
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms / peak)
    idx = np.where(db > thresh_db)[0]
    if len(idx) == 0:
        return 0, len(audio)
    return idx[0] * frame, min(len(audio), (idx[-1] + 1) * frame)


def trim_padded(raw, words):
    """Trim to PAD_S of head/tail silence; shift word timings (in place) to match."""
    s0, s1 = trim_bounds(raw)
    pad = int(PAD_S * SR)
    a, b = max(0, s0 - pad), min(len(raw), s1 + pad)
    # pad with true silence if the model's own head/tail was shorter than PAD_S
    head = np.zeros(pad - (s0 - a), np.float32)
    tail = np.zeros(pad - (b - s1), np.float32)
    audio = np.concatenate([head, raw[a:b], tail])
    shift = (a - len(head)) / SR
    dur = len(audio) / SR
    for w in words:
        w["start_s"] = min(max(0.0, w["start_s"] - shift), dur)
        w["end_s"] = min(max(w["start_s"], w["end_s"] - shift), dur)
    return audio


def limit(x, ceil, win_s=0.005):
    """Lookahead peak limiter: running-min gain over a window, then smoothed."""
    w = max(1, int(win_s * SR))
    need = np.minimum(1.0, ceil / (np.abs(x) + 1e-12))
    if need.min() >= 1.0:
        return x
    padded = np.pad(need, (w, w), constant_values=1.0)
    g = np.lib.stride_tricks.sliding_window_view(padded, 2 * w + 1).min(axis=1)
    kernel = np.ones(2 * w + 1) / (2 * w + 1)
    g = np.convolve(np.pad(g, (w, w), mode="edge"), kernel, mode="valid")
    g = np.minimum(g, need)  # guarantee the ceiling after smoothing
    return x * g


def normalize(audio, meter):
    """Gain to TARGET_LUFS, peak-limit to PEAK_CEIL_DB, re-trim gain (2 passes)."""
    ceil = 10 ** (PEAK_CEIL_DB / 20)
    out = audio.astype(np.float64)
    for _ in range(3):
        loud = meter.integrated_loudness(out)
        out = limit(out * (10 ** ((TARGET_LUFS - loud) / 20)), ceil)
    return out.astype(np.float32)


def stats(audio, meter, n_words):
    dur = len(audio) / SR
    peak = float(np.max(np.abs(audio)))
    return {
        "duration_s": round(dur, 3),
        "lufs": round(float(meter.integrated_loudness(audio)), 2),
        "peak_dbfs": round(20 * math.log10(peak + 1e-12), 2),
        "words_per_s": round(n_words / dur, 2),
    }


def rnd(words):
    return [{"word": w["word"], "start_s": round(w["start_s"], 3), "end_s": round(w["end_s"], 3)}
            for w in words]
