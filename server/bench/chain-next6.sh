#!/bin/bash
# After chain-next5: the 4-bit 27B on both 3090s without MTP, and with one draft instead of three. With three drafts it
# peaked at 8 agents and fell at 12-16 with room left in the KV pool (10-18), as the 8-bit 27B with MTP had; these say
# whether the drafter's verify work is the cause, and what a main setup for 12-16 agents should run. Then IQ3_S in
# Strata's own setup for several conversations (4 pipelined slots, 64k each), so Strata's agents rows are not only its
# one-at-a-time default and the two-slot MTP variant.
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next6.sh <chain-next5 pid> &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
[ -n "$1" ] || { echo "chain-next6: no pid to wait on"; exit 1; }   # 10-18: an empty pid skipped the wait, and the
# chain ran beside chain-next4's swarm (both arms failed to start, and a3b-awq was stopped mid-start)
while kill -0 "$1" 2>/dev/null; do sleep 30; done
echo "=== $(date -Is) chain-next6: a3b-awq's swarm again (stopped by the collision above), and q9b-bf16's"
# q9b-bf16 ran out of memory at its own warm-up (bench-arms.sh, swarm arms); a3b-awq was stopped by the collision
./bench-phase.sh swarm a3b-awq q9b-bf16
echo "=== $(date -Is) chain-next6: 8 scouts of the 4-bit 27B at 96k, against the swarms of small ones"
./bench-phase.sh few w4-27b-tp2 8 96000
echo "=== $(date -Is) chain-next6: the 4-bit 27B without MTP, and with one draft"
LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed w4-27b-tp2n:vllm w4-27b-tp2m1:vllm
echo "=== $(date -Is) chain-next6: IQ3_S with 4 batch slots"
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
LEVELS=1,3,6,8 ./bench-phase.sh speed fn-iq3s-2-b4:strata
echo "=== $(date -Is) chain-next6: DONE"
