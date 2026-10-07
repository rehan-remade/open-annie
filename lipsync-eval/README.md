# lipsync-eval

Offline measurement of Annie's mouth against phoneme ground truth. Nothing here ships;
it exists so lipsync constants are measured, not guessed.

## Pipeline

| step | tool | output |
|---|---|---|
| Kokoro lines: exact phone timings from Kokoro's own duration predictor (re-synthesised, checked against the shipped wav) | `../demo/.venv/bin/python kokoro_gt.py` | `out/gt_kokoro.json` |
| espeak phonemes for the live turns (session bus transcripts) and the Kokoro texts | `../demo/.venv/bin/python live_text.py` | `out/live_turns.json`, `out/kokoro_espeak.json` |
| CTC forced alignment with `facebook/wav2vec2-lv-60-espeak-cv-ft` (fp16, ~1.3 GB VRAM) | `uv sync && .venv/bin/python ctc_gt.py` | `out/gt_ctc.json` |
| run the mouth pipelines exactly as capture mode does (virtual clock, 60 fps) | `node --import ./register.mjs providers.mjs clips.json out/providers.json [cfg.json]` | `out/providers.json` |
| score | `.venv/bin/python score.py [out/providers.json] [--ctc]` | table |
| grid search | `.venv/bin/python sweep.py grid.json` | tsv |

Pipelines scored: `before` (energy provider + old `MouthShaper`, the pre-upgrade mouth),
`energy` (energy provider + `mouth.js`, the fallback), `ha` (HeadAudio visemes + energy
loudness + `mouth.js`, the default).

Ground truth: Kokoro lines use Kokoro's durations, shifted by the lag that best aligns their
speech/silence with the audio (measured: -50 ms; Kokoro's decoder runs ~2 frames ahead of
its duration grid). The live take (GPT-Live voice, a different speaker from the Kokoro lines) uses CTC alignment.
`--ctc` scores the Kokoro lines with CTC ground truth too, which measures the GT method.

Metrics (see `score.py` docstring): collapsed 5-vowel accuracy, silence recall, bilabial
closure recall, vowel frames shown shut, and A/V offset (cross-correlation of displayed
aperture with GT openness; positive = mouth leads).

## Results (2026-09-28, `out/score.txt`)

Measured on the previous demo script: 7 Kokoro lines and an earlier live take. The mouth pipeline
has not changed since; `clips.json` now lists the current lines, so rerunning the pipeline above
measures them.

| set | pipeline | vowel5 | silence | closure p/b/m | vowels shown shut | A/V offset |
|---|---|---|---|---|---|---|
| Kokoro (7 lines, exact GT) | before | 26.4% | 100% | 13.6% (3/22) | 4.7% | +13 ms |
| | **HeadAudio + mouth.js** | **34.6%** | 99.2% | **40.9%** (9/22) | 12.1% | +14 ms |
| live GPT-Live take (CTC GT) | before | 21.7% | 97.2% | 20.8% (5/24) | 0.6% | +25 ms* |
| | **HeadAudio + mouth.js** | **31.1%** | 98.0% | **37.5%** (9/24) | 7.4% | +32 ms* |

Chance for vowel5 is 20%. HeadAudio's raw frame accuracy is modest (15 visemes ~18%, 5 vowels ~33%);
most of the gain is pooling + vowel bias + the PP rule in `mouth.js`. Constants were picked on both sets (a small grid, `grid.json`), so these are not held-out
numbers. *CTC ground truth runs ~18 ms
late against Kokoro's exact timings (`score.py --gtcheck`), so the live offsets are ~+14 ms true lead.
