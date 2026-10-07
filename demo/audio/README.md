# Demo voice lines

These are the voice lines for the open-annie keyless preview, generated from `../script.json`. Annie's lines come from `../tts.py`; the user's lines come from `../user_voice.py` (the same lines were spoken to GPT-Live for the recorded session in `../live/`).

## Model and license

- **TTS:** [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) by hexgrad, run locally with the `kokoro` Python package (0.9.4). It is **Apache-2.0**, and the voice packs ship with the model under the same license.
  - Annie uses `af_heart` at speed 1.0.
- **User voice:** OpenAI `gpt-audio-1.5`, voice `cedar`, performing each line from the `direction` in `script.json` (`voices.user` is `openai:gpt-audio-1.5/cedar`). Kokoro read the user's lines cold and sounded flat next to GPT-Live's Annie.
  - These are AI-generated voices: say so wherever the demo is published (OpenAI's usage policies ask for it).
  - Each take is checked against the script with `gpt-4o-transcribe` (a stammer or filler is allowed; a spoken stage direction or a changed word is not) and rejected if it pauses over 0.8 s mid-line. `05_user_feelings` lists `"tighten": ["terrifying."]`: GPT-Live took the breath after that complete sentence as the end of the user's turn, so it is cut to 0.25 s.
  - Not deterministic, so the committed WAVs are the artifact, and `tts.py` keeps them as they are. `01_user_blink` and `05_user_feelings` are the takes picked in the voice audition (`user_voice.py id=take.wav`).
- **G2P:** `misaki[en]` (Apache-2.0). Out-of-vocabulary words fall back to espeak-ng through the pip packages `espeakng-loader` and `phonemizer-fork`, so no system install is needed. espeak-ng is GPL-3.0, but it is only a phonemizer tool here and is not part of the audio.

## Files

- **`<id>.wav`:** 24 kHz, mono, PCM16. Leading and trailing silence is trimmed to 40 ms of padding. Each line is normalized to -16 LUFS integrated (BS.1770, via `pyloudnorm`) and peak-limited to -1 dBFS.
- **`<id>_cut.wav`** (only for a beat with `interrupted_at_s` in `script.json`; the current script has none): the full line cut at the first word boundary at or after `interrupted_at_s`, with a 60 ms fade-out. It keeps the full line's gain rather than being re-normalized.
- **`lines.json`:** one entry per file, in this shape:
  `{id, who, text, file, duration_s, lufs, peak_dbfs, words_per_s, timing, words:[{word, start_s, end_s}]}`.
  The cut entry also has `cut_from`, `interrupted_at_s`, `cut_at_s` and `fade_out_s`.

### Word timings

`timing: "kokoro"` means the word timings are real. They come from Kokoro's own duration predictor: each `KPipeline` result carries tokens with `start_ts` and `end_ts`. The timings are shifted to match the trimmed file.

- Punctuation is attached to the word before it.
- Timings are aligned to the phoneme-duration grid, which is roughly 12 to 25 ms.

If a token ever lacks timestamps, the script falls back to spreading the time across characters and marks the line `timing: "estimated"`.

## Regenerate

```bash
cd demo
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python \
  --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple \
  --index-strategy unsafe-best-match torch
uv pip install --python .venv/bin/python kokoro "misaki[en]" soundfile numpy pyloudnorm requests
.venv/bin/python tts.py         # Annie's preview lines (Kokoro)
.venv/bin/python user_voice.py  # the user's lines (OpenAI; reads OPENAI_API_KEY from the env or ../.env)
```

`user_voice.py 05_user_feelings` re-performs one line; `user_voice.py 05_user_feelings=take.wav` keeps a take you already like (still checked).

The first run downloads the model and voices from Hugging Face, plus the spaCy model `en_core_web_sm`.

`tts.py` is re-runnable: it overwrites everything in `audio/` except the user's lines. The torch seed is fixed, so repeat runs give byte-identical output. CPU is enough, and CUDA is used automatically if a CUDA build of torch is installed.

At the end of a run, the script prints each line's duration, words per second, LUFS and peak. It warns when a line falls outside 2 to 4 words per second or its peak is above -0.5 dBFS.
