# Contributing to open-annie

Thanks for helping. Issues and pull requests are welcome: bug reports, new avatars or motion,
voice and decision providers, and docs.

## Setup

```bash
python3 -m http.server 8080          # the stage: http://localhost:8080/stage/ (no build step)

cd broker                             # the broker and its tests (mocked upstreams, no keys needed)
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m pytest -q

cd demo && npm install && npx playwright install chromium   # the body probe and other QA tools
```

Keys go in `.env` (see `.env.example`), which is gitignored. Never commit keys, and never paste
them into issues.

## Before you open a pull request

- `python3 scripts/check_assets.py` passes (CI runs it, the broker tests and a JavaScript syntax
  check).
- The stage loads without console errors, both keyless (`/stage/`) and as a replay
  (`/stage/?replay=demo/live/session.json&decisions=demo/live/decisions.json`).
- For motion or runtime-body changes, run `node demo/bodyprobe.mjs` and compare the penetration
  and pop numbers before and after.
- Match the surrounding code: plain ES modules in the browser, no build step, comments that say
  why. The shared interfaces are in [docs/contracts.md](docs/contracts.md); update it when you
  change one.

## Assets

Everything under `assets/` is redistributed, so every file needs a licence that allows it, recorded
in its manifest with a sha256 ([assets/README.md](assets/README.md) explains the format and the
gate). No Mixamo data and no purchased characters. If a licence is unclear, leave the asset out.

## The character

`persona/character.md` and `persona/voice.md` define Annie; swap them to make a different
character. Keep the safety lines: she says she is an AI, keeps things suitable for general
audiences, and points to crisis resources when someone mentions self-harm. Voices you add for demos
are AI-generated, so say so wherever you publish them.

## Licence

By contributing, you agree that your contributions are licensed under the Apache License 2.0
(code) or, for assets, under the licence recorded in their manifest.
