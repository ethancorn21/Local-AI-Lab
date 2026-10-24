#!/bin/bash
# After chain-next4: every 27B arm's agents at once, 1 to 16, measured the same way (bench-load.py with the window fix
# and --pregrow, 10-18). The first runs counted only requests that finished inside the window (empty past 6 agents),
# let one level's open requests run into the next, and started every agent on a fresh prompt, so the KV cache never
# carried its working load. Also the two-card pipeline at 131k (it did not fit at 262k), and IQ3_S with the 5060 Ti
# as a helper again, and Strata's agents rows again (the cut at the window's end missed Strata). The old load rows are
# kept beside the new ones as <arm>.load-v1.jsonl.
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next5.sh <chain-next4 pid> &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
while kill -0 "$1" 2>/dev/null; do sleep 30; done
keep_v1() {   # move an arm's old agents-at-once rows aside (every row with "all"); its one-request rows stay
  python3 - "results/$1.load.jsonl" "${2:-}" <<'EOF'
import json, os, sys
f, every = sys.argv[1], sys.argv[2] == "all"
if os.path.exists(f):
    rows = [json.loads(l) for l in open(f)]
    old = [r for r in rows if every or r["test"] == "load"]
    open(f.replace(".load.jsonl", ".load-v1.jsonl"), "a").writelines(json.dumps(r) + "\n" for r in old)
    open(f, "w").writelines(json.dumps(r) + "\n" for r in rows if r not in old)
EOF
}
cold() { sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }
echo "=== $(date -Is) chain-next5: IQ3_S with the 5060 Ti as a helper, cards in the right order"
# chain-next4's run had the layers on the 3090 and the 5060 Ti (the image's own GPUS=0,1,2 won, bench-arms.sh)
sudo systemctl stop matcher-5060ti matcher-roma-5060ti
cold
./bench-phase.sh speed fn-iq3s-2h:strata
echo "=== $(date -Is) chain-next5: Strata's agents again, with requests cut at the window's end"
# every Strata reply closes its connection, so bench-load.py's cut did nothing until 10-18 and each level's open
# requests ran on into the next (fn-q4kxl-2h: 0 tok/s at 6 agents); the one-agent rows stand. These are the baselines
# the placement variants (chain-next4, run with the fix) are compared with.
# The two default splits redo their one-request rows too, from an empty page cache: they first ran after other arms,
# while every placement variant they are compared with started cold (fn-q4kxl-2t, split 27, read a 50k prompt in 174 s
# against the default's 125-133 s, and so did the helper run: the split or the warm cache, 10-18)
for a in fn-iq3s-2 fn-q4kxl-2; do keep_v1 $a all; cold; ./bench-phase.sh speed $a:strata; done
keep_v1 fn-q4kxl-2h
SINGLE=0 ./bench-phase.sh speed fn-q4kxl-2h:strata
echo "=== $(date -Is) chain-next5: the 8-bit 27B's agents again, steady state"
for a in fp8-27b fp8-27b-mtp; do keep_v1 $a; done
SINGLE=0 LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed fp8-27b:vllm fp8-27b-mtp:vllm
echo "=== $(date -Is) chain-next5: the 8-bit 27B as a pipeline, two cards at 131k and three"
LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed fp8-27b-pp:vllm
keep_v1 fp8-27b-pp3
SINGLE=0 LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed fp8-27b-pp3:vllm
sudo systemctl start matcher-5060ti matcher-roma-5060ti
echo "=== $(date -Is) chain-next5: DONE"
