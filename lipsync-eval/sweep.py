#!/usr/bin/env python3
"""Grid-search mouth constants: providers.mjs per config, score.py --json, one line each.
  ../demo/.venv/bin/python sweep.py grid.json > out/sweep.tsv     (or the eval .venv)
grid.json: {"name": {cfg for providers.mjs}, ...}
"""
import json, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
grid = json.loads(Path(sys.argv[1]).read_text())
(HERE / "out/sweep").mkdir(parents=True, exist_ok=True)
keys = ["vowel5", "sil", "closure", "shut_on_vowel", "open_vowel", "open_cons", "offset_ms", "xcorr"]
print("name\tset\t" + "\t".join(keys))
for name, cfg in grid.items():
    c = HERE / f"out/sweep/{name}.cfg.json"; o = HERE / f"out/sweep/{name}.json"
    c.write_text(json.dumps(cfg))
    subprocess.run(["node", "--import", "./register.mjs", "providers.mjs", "clips.json", str(o), str(c)], cwd=HERE, check=True, capture_output=True)
    t = json.loads(subprocess.run([sys.executable, "score.py", str(o), "--json"], cwd=HERE, check=True, capture_output=True, text=True).stdout)
    for k, row in t.items():
        if k.endswith("/ha") or k.endswith("/energy") or k.endswith("/headaudio-frames"):
            vals = [row.get(x, "") for x in keys] if "frames" not in k else [row["cls15"], row["cls5_soft"], row["cls5_vote"]] + [""] * 5
            print(f"{name}\t{k}\t" + "\t".join(str(v) for v in vals), flush=True)
