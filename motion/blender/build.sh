#!/usr/bin/env bash
# Build the annie-blender pack: every clip keyed, collision-fixed and baked in parallel Blender
# workers, then pack.json and the asset gate.
#   motion/blender/build.sh [workers]        (default 4; BLENDER=/path/to/blender)
set -euo pipefail
cd "$(dirname "$0")/../.."
BLENDER=${BLENDER:-blender}
command -v "$BLENDER" >/dev/null || BLENDER="$HOME/.local/bin/blender"
N=${1:-4}
mkdir -p motion/raw/blender/logs
# Balance the clips across workers by duration (longest first).
mapfile -t CLIPSETS < <(python3 - "$N" <<'PY'
import sys
sys.path.insert(0, "motion/blender")
import clips
n = int(sys.argv[1])
bins = [[0.0, []] for _ in range(n)]
for c in sorted(clips.CLIPS, key=lambda c: -c["duration"]):
    b = min(bins, key=lambda b: b[0])
    b[0] += c["duration"]
    b[1].append(c["name"])
for _, names in bins:
    print(",".join(names))
PY
)
pids=()
for i in "${!CLIPSETS[@]}"; do
  "$BLENDER" -b -noaudio -y --python motion/blender/export.py -- --only "${CLIPSETS[$i]}" --no-pack \
    > "motion/raw/blender/logs/worker$i.log" 2>&1 &
  pids+=($!)
done
fail=0
for p in "${pids[@]}"; do wait "$p" || fail=1; done
grep -hE "^[a-z_0-9]+: [0-9]+ frames|collide|QA|Error|Traceback" motion/raw/blender/logs/worker*.log || true
[ "$fail" = 0 ] || { echo "a worker failed (motion/raw/blender/logs/)"; exit 1; }
python3 motion/blender/pack.py
python3 scripts/check_assets.py | tail -3
