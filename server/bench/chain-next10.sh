#!/bin/bash
# Stopped 10-21 after its Q4 placements: chain-next11 took over (Q4 tied IQ3_S on quality; the coding order went to IQ3_S).
# Takes over from chain-next8 after its realwork runs (10-20), before chain-next7. chain-next8's first two runs showed
# where Q4's drive reads come from: pinning up to 23 GiB (-2hr) took the answers from 29 to 53 tok/s and the drive from
# 969 to 603 GB, but the guard cut the pin to 20.96 GiB, 2.8 GiB of the 23.7 outside VRAM stayed on the drive, and the
# box ran at 0.2 GiB available (the engine grows ~5 GiB once serving), so more RAM is not there to take. And its next
# arms were not what they said: Strata's setup rewrites exactly "--prefill 2048" to "auto" (-2hrp ran as -2hr again,
# -2hrpk as -2hr at 131k). Here the room comes from VRAM instead (bench-arms.sh, fn-q4kxl-2z-<letters>): 131k or 64k of
# context instead of 262k, no expert held twice (STRATA_DISJOINT_ADAPT: a 3090 promoting an expert the 5060 Ti holds
# reads it from the drive), half the slots lent to prompts; then a coding order on the best of them, then the hard
# problems on the fastest Q4 placement. realwork now also times one cold 50k prompt and logs RAM and swap.
# Reordered 10-20 16:15, the owner: "lets just wait to see if q4 is even worth it" (asked whether to fetch a 63 GiB NVFP4
# build that would fit the cards and RAM whole). Worth it means better answers than IQ3_S, which runs at twice the
# speed; one try per problem did not settle it (held-out: Q4 better on 6 tasks, IQ3_S on 2, p = 0.29; hard: IQ3_S
# 16/24, the best of every arm, Q4 not yet run). So first the 24 hard problems 3 tries each on both (IQ3_S keeps its
# first try and adds two, on the arm that made it), then the placements, which only matter if Q4 is worth it.
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next10.sh <pid of chain-next8's realwork bench-phase> &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
[ -n "$1" ] || { echo "chain-next10: no pid to wait on"; exit 1; }
while kill -0 "$1" 2>/dev/null; do sleep 30; done
sudo systemctl stop matcher-5060ti matcher-roma-5060ti   # stopped by chain-next8 already; the helper arms need the 5060 Ti
fastest_q4() {   # the Q4 placement with the fastest median decode on the 4 real answers
  python3 - <<'EOF'
import glob, json, statistics
best = (0, "fn-q4kxl-2hr")
for f in glob.glob("results/fn-q4kxl-*-real.gen.jsonl"):
    t = [json.loads(l).get("decode_tps") or 0 for l in open(f)]
    if len(t) == 4 and statistics.median(t) > best[0]:
        best = (statistics.median(t), f.split("/")[1].removesuffix("-real.gen.jsonl"))
print(best[1])
EOF
}
best=$(fastest_q4)
echo "=== $(date -Is) chain-next10: is Q4 worth it: the hard problems 3 tries each, Q4 on $best and IQ3_S"
HARD_ATTEMPTS=3 ./bench-phase.sh hard "$best:strata" fn-iq3s-2:strata
python3 lcb-quick.py compare "$best-lcbhard" fn-iq3s-2-lcbhard
python3 lcb-quick.py compare fn-q4kxl-3 fn-iq3s-2
echo "=== $(date -Is) chain-next10: Q4 with the room taken from VRAM: 131k, 64k, no duplicate experts, smaller prompt loans"
./bench-phase.sh realwork fn-q4kxl-2z-kd:strata fn-q4kxl-2z-qd:strata fn-q4kxl-2z-kdp:strata
# The base for the coding order: 64k only if it decodes 5% faster than 131k (64k is the owner's floor, 131k twice it);
# p only if its cold 50k prompt was faster (it changes the prompt path, not decode)
base=$(python3 - <<'EOF'
import json, statistics
def dec(a):
    try:
        t = [json.loads(l).get("decode_tps") or 0 for l in open(f"results/fn-q4kxl-2z-{a}-real.gen.jsonl")]
    except OSError:
        return 0
    return statistics.median(t) if len(t) == 4 else 0
def ttft(a):
    try:
        for l in open(f"results/fn-q4kxl-2z-{a}-real.load.jsonl"):
            r = json.loads(l)
            if r.get("test") == "50k_cold" and "error" not in r and r.get("ttft_s"):
                return r["ttft_s"]
    except OSError:
        pass
    return 1e9
ctx = "q" if dec("qd") > 1.05 * dec("kd") else "k"
print(ctx + "d" + ("p" if ttft("kdp") < ttft("kd") else ""))
EOF
)
echo "=== $(date -Is) chain-next10: a coding order on fn-q4kxl-2z-$base"
./bench-phase.sh codeprofile "fn-q4kxl-2z-${base}s"
if [ -f results/codeprofile.ok ]; then
  ./bench-phase.sh realwork "fn-q4kxl-2z-${base}c:strata"
else
  echo "=== $(date -Is) chain-next10: no coding order saved, its run skipped"
fi
sudo systemctl start matcher-5060ti matcher-roma-5060ti
echo "=== $(date -Is) chain-next10: DONE"
