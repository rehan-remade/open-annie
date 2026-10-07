#!/usr/bin/env python3
"""Perform the demo's user lines with OpenAI gpt-audio (voice cedar).

Kokoro (tts.py) reads every line cold, and next to GPT-Live's Annie the user's side
sounded flat. gpt-audio-1.5 performs each line from a short direction in script.json.
It sometimes speaks a direction aloud ("Annie... small pause..."), so every take is
transcribed with gpt-4o-transcribe and retried until it says the script (a stammer
or filler is fine) with no pause over MAX_PAUSE (a long silence mid-line can hand
GPT-Live the turn).
Word timings come from whisper-1. id=take.wav keeps a take you liked (still checked).
GPT-Live answers a complete sentence followed by a breath, even mid-line ("That's
terrifying. [0.6 s] So..."): a beat's "tighten" lists words whose following silence
is cut to TIGHT_GAP.

Reads demo/script.json beats whose voice is "openai:<model>/<voice>" and writes
  demo/audio/<id>.wav    24 kHz mono PCM16, trimmed (~40 ms padding), ~-16 LUFS
  their demo/audio/lines.json entries (same shape as tts.py, timing "whisper")
Needs OPENAI_API_KEY (env or the repo .env; the live demo uses the same key).
Not deterministic: the committed WAVs are the artifact, and tts.py leaves them alone.

Usage:  demo/.venv/bin/python demo/user_voice.py [beat ids...] [id=take.wav ...]
"""
import base64
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import requests
import soundfile as sf

from lines_util import SR, normalize, rnd, stats, trim_padded

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "script.json"
OUT = HERE / "audio"
API = "https://api.openai.com/v1"
TRIES = 4
MAX_PAUSE = 0.8  # s of silence inside a line
TIGHT_GAP = 0.25  # s of silence left after a word in a beat's "tighten"
SYSTEM = ("You are a voice actor recording lines for a demo. Speak ONLY the exact words of the user's line, "
          "verbatim, nothing before or after. Never say directions, sound effects or descriptions aloud: "
          "perform them only through tone, pacing and breath. Direction (never read aloud): ")


def api_key():
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    env = HERE.parent / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        key, _, value = line.partition("=")
        if key.strip() == "OPENAI_API_KEY":
            return value.strip().strip("'\"")
    sys.exit("OPENAI_API_KEY is not set (environment or repo .env)")


def perform(s, model, voice, text, direction):
    r = s.post(f"{API}/chat/completions", timeout=120, json={
        "model": model, "modalities": ["text", "audio"], "audio": {"voice": voice, "format": "wav"},
        "messages": [{"role": "system", "content": SYSTEM + direction}, {"role": "user", "content": text}]})
    r.raise_for_status()
    return base64.b64decode(r.json()["choices"][0]["message"]["audio"]["data"])


def transcribe(s, wav, model, **form):
    r = s.post(f"{API}/audio/transcriptions", timeout=120,
               files={"file": ("line.wav", wav, "audio/wav")}, data={"model": model, **form})
    r.raise_for_status()
    return r.json()


def decode(wav):
    """The API's WAV (a streaming header with no length) -> float32 mono at SR."""
    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         input=wav, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def longest_pause(x, hop=240, floor_db=-35.0):
    """Longest silence (s) between the line's first and last voiced 10 ms frames."""
    n = len(x) // hop
    db = 20 * np.log10(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
    v = np.flatnonzero(db > db.max() + floor_db)
    return float(np.diff(v).max() - 1) * hop / SR if len(v) > 1 else 0.0


def tighten(x, words, after, hop=240, floor_db=-35.0):
    """Cut the silence after each word in `after` down to TIGHT_GAP (10 ms crossfade);
    later word timings move with the cut."""
    for i, w in enumerate(words[:-1]):
        if w["word"] not in after:
            continue
        # the longest quiet run between the word and the next one (0.1 s of slack for timings)
        a = max(0, int((w["end_s"] - 0.1) * SR))
        seg = x[a: int((words[i + 1]["start_s"] + 0.1) * SR)]
        n = len(seg) // hop
        db = 20 * np.log10(np.sqrt((seg[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
        quiet = db < 20 * np.log10(np.abs(x).max() + 1e-9) + floor_db
        best, run = (0, 0), 0
        for k, q in enumerate(quiet):
            run = run + 1 if q else 0
            if run > best[1] - best[0]:
                best = (k + 1 - run, k + 1)
        q0, q1 = a + best[0] * hop, a + best[1] * hop
        cut = (q1 - q0) - int(TIGHT_GAP * SR)
        if cut <= 0:
            continue
        c0, f = q0 + int(TIGHT_GAP * SR / 2), int(0.01 * SR)
        fade = np.linspace(1.0, 0.0, f, dtype=np.float32)
        mixed = x[c0 - f: c0] * fade + x[c0 + cut - f: c0 + cut] * (1 - fade)
        x = np.concatenate([x[: c0 - f], mixed, x[c0 + cut:]])
        for v in words[i + 1:]:
            v["start_s"] -= cut / SR
            v["end_s"] -= cut / SR
    return x


def norm_words(text):
    return re.sub(r"[^a-z0-9' ]", " ", text.lower().replace("’", "'")).split()


FILLERS = {"um", "uh", "er", "erm", "ah", "ha", "haha", "heh", "hehe"}


def said(text):
    """Words compared between a take and the script: a stammer ("that's... that's") or a
    filler is acting; a spoken direction ("small pause") or a changed word is not."""
    out = []
    for w in norm_words(text):
        if w not in FILLERS and not (out and out[-1] == w):
            out.append(w)
    return out


def word_timings(s, wav, text):
    """Script words (punctuation attached, as tts.py writes them) timed by whisper-1.
    A performed stammer or filler the script lacks ("that's... that's terrifying")
    belongs to the script word after it."""
    script = text.split()
    got = transcribe(s, wav, "whisper-1", response_format="verbose_json",
                     **{"timestamp_granularities[]": "word"}).get("words", [])
    a = [" ".join(norm_words(w)) for w in script]
    b = [" ".join(norm_words(g["word"])) for g in got]
    times, after = {}, 0  # after: first whisper word past the last match
    for i, j, n in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(n):
            start = got[after]["start"] if k == 0 and after < j else got[j + k]["start"]
            times[i + k] = (start, got[j + k]["end"])
        if n:
            after = j + n
    if len(times) == len(script):
        return [{"word": w, "start_s": times[i][0], "end_s": times[i][1]} for i, w in enumerate(script)], "whisper"
    # Whisper heard different words: spread the voiced span over the script by characters.
    t0, t1 = (got[0]["start"], got[-1]["end"]) if got else (0.0, len(decode(wav)) / SR)
    total = sum(len(w) for w in script)
    words, t = [], t0
    for w in script:
        d = (t1 - t0) * len(w) / total
        words.append({"word": w, "start_s": t, "end_s": t + d})
        t += d
    return words, "estimated"


def main():
    cfg = json.loads(SCRIPT.read_text())
    takes = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    only = {a for a in sys.argv[1:] if "=" not in a} | set(takes)
    lines_path = OUT / "lines.json"
    lines = {l["id"]: l for l in json.loads(lines_path.read_text())} if lines_path.exists() else {}
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {api_key()}"
    meter = pyln.Meter(SR)  # BS.1770
    done = []

    for beat in cfg["beats"]:
        bid, who, text = beat["id"], beat["who"], beat["text"]
        voice = cfg["voices"][who]
        if not voice.startswith("openai:") or (only and bid not in only):
            continue
        model, _, name = voice[len("openai:"):].partition("/")
        direction = f'{cfg.get("direction", {}).get(who, "")} {beat.get("direction", "")}'.strip()
        for attempt in range(1, TRIES + 1):
            wav = Path(takes[bid]).read_bytes() if bid in takes else perform(s, model, name, text, direction)
            heard = transcribe(s, wav, "gpt-4o-transcribe")["text"]
            gap = longest_pause(decode(wav))
            if said(heard) == said(text) and gap <= MAX_PAUSE:
                break
            problem = f"said {heard!r}" if said(heard) != said(text) else f"paused {gap:.2f} s"
            if bid in takes:
                sys.exit(f"{bid}: {takes[bid]} {problem}")
            print(f"{bid}: take {attempt} {problem}; retrying", file=sys.stderr)
        else:
            sys.exit(f"{bid}: no clean take in {TRIES} tries")
        words, timing = word_timings(s, wav, text)
        audio = normalize(trim_padded(tighten(decode(wav), words, set(beat.get("tighten", []))), words), meter)
        fname = f"{bid}.wav"
        sf.write(OUT / fname, audio, SR, subtype="PCM_16")
        lines[bid] = {"id": bid, "who": who, "text": text, "file": fname, **stats(audio, meter, len(words)),
                      "timing": timing, "voice": voice, "direction": direction, "words": rnd(words)}
        done.append(bid)

    # Script order; a cut line (tts.py) right after the line it was cut from.
    order = {b["id"]: i for i, b in enumerate(cfg["beats"])}
    key = lambda l: order.get(l.get("cut_from", l["id"]), len(order)) + (0.5 if "cut_from" in l else 0)
    lines_path.write_text(json.dumps(sorted(lines.values(), key=key), indent=2, ensure_ascii=False) + "\n")

    print(f"{'id':24} {'dur':>6} {'wps':>5} {'LUFS':>7} {'peak':>6}  timing")
    for bid in done:
        e = lines[bid]
        print(f"{bid:24} {e['duration_s']:6.2f} {e['words_per_s']:5.2f} {e['lufs']:7.2f} {e['peak_dbfs']:6.2f}  {e['timing']}")


if __name__ == "__main__":
    main()
