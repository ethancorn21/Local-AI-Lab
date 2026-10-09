#!/bin/bash
# run_mutant.sh <id> : copy the clone, apply mutant <id>, run the suite (timing tests excluded), keep the junit xml.
SP=${SP:?set SP to a work dir holding fpmut/ (a clone with .venv and node_modules), pw/ (playwright browsers), mutants.json}
id=$1; d=$SP/mut/w$id; rm -rf "$d"; mkdir -p $SP/mut
cp -c -R $SP/fpmut "$d" || exit 1
python3 - "$d" "$id" "$SP/mutants.json" <<'PY'
import json, sys
d, i, mf = sys.argv[1], int(sys.argv[2]), sys.argv[3]
m = json.load(open(mf))[i]
p = f"{d}/{m['file']}"
lines = open(p).read().splitlines(keepends=True)
assert lines[m['line'] - 1].rstrip("\n") == m['orig'], "orig mismatch"
lines[m['line'] - 1] = m['new'] + "\n"
open(p, "w").write("".join(lines))
PY
cd "$d" && export PATH=$SP/fpmut/.venv/bin:$PATH PLAYWRIGHT_BROWSERS_PATH=$SP/pw
s=$(date +%s)
python -m pytest -q -p no:cacheprovider --ignore=tests/test_perf.py --junitxml=$SP/mut/$id.xml > $SP/mut/$id.out 2>&1
echo "$id rc=$? secs=$(( $(date +%s) - s ))" >> $SP/mut/done.txt
cd $SP && rm -rf "$d"
