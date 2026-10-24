#!/bin/bash
# After chain-next6, before chain-next7 (10-20): the 4-bit Flash-Next off the NVMe drive. The owner: "the goal is to try
# to not use any of the nvme at all. It needs to be faster." Q4 passed 36 of 42 on the held-out set, the most of any arm,
# but its answers decoded at a median 13.4 tok/s against the speed test's 35: on three cards every answer read ~95 GB
# from the drive (the engine's own count; 25 TB over the run), while IQ3_S on the helper split reads ~0.1 GB a request.
# The speed test repeats its prompts, so their experts stay in the page cache; real answers do not. On the helper split
# the cards hold 48 GiB of Q4's experts and 23.5 GiB stay outside VRAM, but setup gave the RAM copy 8 GiB of the 27.5
# GiB free. bench-arms.sh, fn-q4kxl-2hr*: each arm adds one change (pin up to 23 GiB, 2,048-token prompt chunks, 131k,
# a coding order learned from prompts that share nothing with the held-out set, no duplicate experts), each run the
# same 4 held-out tasks from an empty page cache (bench-phase.sh realwork: decode on real answers and the GB the drive
# read). IQ3_S on the helper split is the reference that already stays off the drive. Then the hard problems on the
# fastest Q4 placement (the weights, so the answers' quality, are the same on every placement).
# Stopped after its first realwork call (10-20, 15:35): chain-next10.sh took over the rest. -2hrp's "--prefill 2048" is
# rewritten to "auto" by Strata's setup, and -2hr's log showed the room must come from VRAM, not RAM.
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next8.sh <chain-next6 pid> &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
[ -n "$1" ] || { echo "chain-next8: no pid to wait on"; exit 1; }
while kill -0 "$1" 2>/dev/null; do sleep 30; done
sudo systemctl stop matcher-5060ti matcher-roma-5060ti   # the helper arms need the 5060 Ti
echo "=== $(date -Is) chain-next8: Flash-Next off the NVMe drive, real answers per placement"
./bench-phase.sh realwork fn-q4kxl-2h:strata fn-q4kxl-2hr:strata fn-q4kxl-2hrp:strata fn-q4kxl-2hrpk:strata fn-iq3s-2h:strata
echo "=== $(date -Is) chain-next8: a coding order for the tiers"
./bench-phase.sh codeprofile fn-q4kxl-2hrpks
if [ -f results/codeprofile.ok ]; then
  ./bench-phase.sh realwork fn-q4kxl-2hrpkc:strata fn-q4kxl-2hrpkcd:strata
else
  echo "=== $(date -Is) chain-next8: no coding order saved, its runs skipped"
fi
echo "=== $(date -Is) chain-next8: the 4-bit Flash-Next on the hard problems, on its fastest placement"
best=$(python3 - <<'EOF'
import glob, json, statistics
best = (0, "fn-q4kxl-2h")
for f in glob.glob("results/fn-q4kxl-*-real.gen.jsonl"):
    t = [json.loads(l).get("decode_tps") or 0 for l in open(f)]
    if len(t) == 4 and statistics.median(t) > best[0]:
        best = (statistics.median(t), f.split("/")[1].removesuffix("-real.gen.jsonl"))
print(best[1])
EOF
)
echo "=== $(date -Is) chain-next8: fastest Q4 placement on real answers: $best"
./bench-phase.sh hard "$best:strata"
sudo systemctl start matcher-5060ti matcher-roma-5060ti
echo "=== $(date -Is) chain-next8: DONE"
