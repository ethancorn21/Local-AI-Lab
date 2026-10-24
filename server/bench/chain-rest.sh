#!/usr/bin/env bash
# 2026-10-18: everything after the two-card reruns (chain2), in one queue: IQ3_S's speed runs (its first two starts
# failed on a stale config baked into the image, see bench-arms.sh), the variants, then quality on the fast arms,
# the swarm, and quality on the 4-bit Flash-Next last (UD-Q4_K_XL streams experts from the SSD at ~30 tok/s: ~6 h for
# 42 answers), so the swarm's results come first. Quality depends on the weights, not the cards or the drafter: each
# model's quality run uses its fastest arm (pick-arm.py).
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
until grep -q "chain2: DONE" results/chain.log; do sleep 30; done
three() { case $1 in *-3) sudo systemctl "$2" matcher-5060ti matcher-roma-5060ti ;; esac; }   # a 3-card arm needs the 5060 Ti
echo "=== $(date -Is) chain-rest: IQ3_S speed, variants"
./bench-phase.sh arms fn-iq3s-2:strata fp8-27b-mtp:vllm fn-iq3s-2-p2:strata
three fn-iq3s-3 stop
./bench-phase.sh arms fn-iq3s-3:strata
three fn-iq3s-3 start
echo "=== $(date -Is) chain-rest: quality, fast arms"
fp8=$(python3 pick-arm.py fp8-27b-mtp fp8-27b); iq3=$(python3 pick-arm.py fn-iq3s-2 fn-iq3s-3)
echo "picked $fp8 $iq3"
three "$iq3" stop
./bench-phase.sh 3 "$fp8:vllm" "$iq3:strata" fn-iq2xs-2:strata
three "$iq3" start
echo "=== $(date -Is) chain-rest: swarm"
./bench-phase.sh 4
q4=$(python3 pick-arm.py fn-q4kxl-3 fn-q4kxl-2)
echo "=== $(date -Is) chain-rest: quality, $q4"
three "$q4" stop
./bench-phase.sh 3 "$q4:strata"
three "$q4" start
echo "=== $(date -Is) chain-rest: DONE"
