#!/usr/bin/env bash
# 2026-10-18: replaces chain-next3.sh from the end of the hard coding problems on (chain-next4.sh <pid of that
# bench-phase.sh>). Everything chain-next3.sh had (alternatives to every setting that was taken as a limit, after the
# owner caught the 5060 Ti judged on Strata's default split alone), plus the 4-bit 27B the owner approved: one copy
# across both 3090s against two copies, one per card, for coding agents and for the swarm, and its quality.
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
while kill -0 "$1" 2>/dev/null; do sleep 30; done
three() { case $1 in *-3|*-2h|*-pp3*|q4b-5060) sudo systemctl "$2" matcher-5060ti matcher-roma-5060ti ;; esac; }   # needs the 5060 Ti
cold() { sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }   # each Strata variant starts from the same empty page cache
TWO=http://127.0.0.1:8093/v1,http://127.0.0.1:8094/v1
echo "=== $(date -Is) chain-next4: capped coding answers again at 32k"
./bench-phase.sh redo fp8-27b-mtp:vllm fn-iq3s-2:strata fn-iq2xs-2:strata
echo "=== $(date -Is) chain-next4: more agents on the 8-bit 27B"
SINGLE=0 LEVELS=8,12,16 ./bench-phase.sh speed fp8-27b-mtp:vllm fp8-27b:vllm
echo "=== $(date -Is) chain-next4: the 8-bit 27B as a pipeline"
LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed fp8-27b-pp:vllm fp8-27b-pp-mtp:vllm
three fp8-27b-pp3 stop
LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed fp8-27b-pp3:vllm fp8-27b-pp3-mtp:vllm
echo "=== $(date -Is) chain-next4: Strata with the 5060 Ti as an expert helper"
for a in fn-iq3s-2h fn-q4kxl-2h; do cold; ./bench-phase.sh speed "$a:strata"; done   # the 5060 Ti's matchers still stopped
three fp8-27b-pp3 start
echo "=== $(date -Is) chain-next4: the 4-bit 27B, one copy on two cards against one copy per card"
LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed w4-27b-tp2:vllm
BENCH_URL=$TWO LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed w4-27b-x2:vllm
./bench-phase.sh 3 w4-27b-tp2:vllm
./bench-phase.sh hard w4-27b-tp2:vllm
echo "=== $(date -Is) chain-next4: Strata placement variants"
for a in fn-iq3s-2s fn-iq3s-2t fn-iq3s-2-bm fn-q4kxl-2t; do cold; ./bench-phase.sh speed "$a:strata"; done
echo "=== $(date -Is) chain-next4: swarm"
./bench-phase.sh 4
./bench-phase.sh swarm w4-27b-tp2b
SWARM_URL=http://127.0.0.1:8082/v1,http://127.0.0.1:8083/v1 ./bench-phase.sh swarmload w4-27b-x2b
three q4b-5060 stop
./bench-phase.sh swarmload q4b-5060
three q4b-5060 start
q4=$(python3 pick-arm.py fn-q4kxl-3 fn-q4kxl-2 fn-q4kxl-2t fn-q4kxl-2h)
echo "=== $(date -Is) chain-next4: quality, $q4"
three "$q4" stop
./bench-phase.sh 3 "$q4:strata"
three "$q4" start
echo "=== $(date -Is) chain-next4: DONE"
