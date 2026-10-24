#!/usr/bin/env bash
# 2026-10-18: replaces chain-rest.sh from the end of the fast arms' quality run on (chain-next.sh <pid of that
# bench-phase.sh>): the hard LiveCodeBench problems on the main candidates and a weak reference first, because the
# held-out set could not tell the 8-bit 27B from IQ3_S (34 vs 32 of 42); then the swarm and the 4-bit Flash-Next's
# quality, as chain-rest.sh had them.
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
while kill -0 "$1" 2>/dev/null; do sleep 30; done
three() { case $1 in *-3) sudo systemctl "$2" matcher-5060ti matcher-roma-5060ti ;; esac; }   # a 3-card arm needs the 5060 Ti
echo "=== $(date -Is) chain-next: hard coding problems"
./bench-phase.sh hard fp8-27b-mtp:vllm fn-iq3s-2:strata fn-iq2xs-2:strata
echo "=== $(date -Is) chain-next: swarm"
./bench-phase.sh 4
q4=$(python3 pick-arm.py fn-q4kxl-3 fn-q4kxl-2)
echo "=== $(date -Is) chain-next: quality, $q4"
three "$q4" stop
./bench-phase.sh 3 "$q4:strata"
three "$q4" start
echo "=== $(date -Is) chain-next: DONE"
