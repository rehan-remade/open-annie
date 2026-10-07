"""Synthesise the dance beat (CC0, made here): kick on the dance clip's dips, offbeat hats, a soft chord stab.
usage: beat.py out.wav seconds rate   (rate = the clip's playback rate; 120 bpm at rate 1)"""
import sys, wave
import numpy as np

out, secs, rate = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
sr = 48000
n = int(secs * sr)
t = np.arange(n) / sr
beat = 0.5 / rate  # seconds per beat
first = 0.25 / rate  # the clip's first hip dip
y = np.zeros(n)
rng = np.random.default_rng(3)

def place(sig, at):
    i = int(at * sr)
    if i >= n: return
    m = min(len(sig), n - i)
    y[i:i + m] += sig[:m]

k = np.arange(int(0.35 * sr)) / sr
kick = np.sin(2 * np.pi * (48 * k + 90 * (1 - np.exp(-k * 30)) / 30)) * np.exp(-k * 9)
hat = rng.standard_normal(int(0.05 * sr)) * np.exp(-np.arange(int(0.05 * sr)) / sr * 90)
hat = np.diff(hat, prepend=0) * 0.35
c = np.arange(int(beat * 1.6 * sr)) / sr
chords = [[220, 277.2, 329.6], [246.9, 311.1, 370], [196, 246.9, 293.7], [220, 277.2, 329.6]]
for b in range(int(secs / beat) + 1):
    at = first + b * beat
    place(kick * 0.9, at)
    place(hat, at + beat / 2)
    if b % 2 == 0:
        f = chords[(b // 2) % 4]
        stab = sum(np.sin(2 * np.pi * fr * c) + 0.3 * np.sin(4 * np.pi * fr * c) for fr in f) / 4
        place(stab * np.exp(-c * 5) * 0.35, at)
fade = np.clip((secs - t) / 0.8, 0, 1) * np.clip(t / 0.05, 0, 1)
y = y * fade
y = y / (np.abs(y).max() + 1e-9) * 0.7
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((y * 32767).astype(np.int16).tobytes())
