#!/bin/bash
# After chain-next11, which took over from chain-next10 and it from chain-next8 (10-20 and 10-21: moved behind them, so the Flash-Next placements report in a day, not four): the swarm result made tight (10-18, the owner: "I want this result tight. Use all the compute you
# want"). The swarm battery ran each setting once, and its 16- and 32-scout runs measured the 32k cap more than the
# model (the 4B's scouts filled their context with what they had read and stopped with their areas half read). Here:
# every swarm model again on one grid, 3 runs for the main settings, with rolling context (scout.py --rolling) beside the
# battery's own; the 27B as 8 large scouts again; the 5060 Ti's own swarm (the 4B, and the 9B in 8 bits); main and
# swarm at once; then the 27B checking every run's reports, the second tier the service would use.
# All of these run under scout.py's rules 2 (10-19, named -v2): the battery's A3B read its area in batches of reads
# and hit the cap before reporting, so the brief now says to report as you go and a read that would overfill the
# context is refused. The 27B's rules-1 runs of 100 scouts and thinking were skipped in chain-next4 (skip-tp2b.sh).
# All at 64k since 10-19 (the owner: "16k context is useless, so is 32k. I would say 64k is the minimum"): the grid is
# 100 scouts x3 and 32 x3 rolling, 100 not rolling x1 (the battery's 64k run under rules 2), 32 thinking x1; the 27B
# 32 and 16 x3 and 32 thinking (100 of it overran its pool: 64 took hours at rules 1). The 5060 Ti's runs time the
# card (same weights as the 3090 runs, or the 9B in 8 bits). First, the main candidates at 128k conversations (they
# were measured at up to 40k), and a full-64k sweep for the swarm arms swept before 10-19 (bench-phase.sh swarm_load).
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next7.sh <chain-next8 pid> &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
[ -n "$1" ] || { echo "chain-next7: no pid to wait on"; exit 1; }
while kill -0 "$1" 2>/dev/null; do sleep 30; done
GRID="100:64000:2:off:80:1-3 32:64000:2:off:80:1-3 100:64000:0:off:40:1-1 32:64000:2:on:80:1-1"
echo "=== $(date -Is) chain-next7: main at 128k conversations"
CAP=128000 SINGLE=0 LEVELS=1,2,4,6,8,12 ./bench-phase.sh speed w4-27b-tp2:vllm w4-27b-tp2n:vllm w4-27b-tp2m1:vllm
CAP=128000 SINGLE=0 LEVELS=1,2,4,6,8 ./bench-phase.sh speed fp8-27b-mtp:vllm
CAP=128000 SINGLE=0 LEVELS=1,2,4,6,8 BENCH_URL=http://127.0.0.1:8093/v1,http://127.0.0.1:8094/v1 \
  ./bench-phase.sh speed w4-27b-x2:vllm
echo "=== $(date -Is) chain-next7: the small swarms on the grid at 64k"
for a in q4b-bf16-dp a3b-fp8 a3b-awq q9b-bf16; do
  case $a in q4b-bf16-dp|a3b-fp8) ./bench-phase.sh load64 $a ;; esac
  ./bench-phase.sh scouts $a $GRID
done
echo "=== $(date -Is) chain-next7: the 4-bit 27B as a swarm at 64k, 32 and 16 scouts, and 32 thinking"
./bench-phase.sh load64 w4-27b-tp2b
# one copy per card's 64k sweep failed in chain-next4 (bench-load.py, Fit to the server): again
SWARM_URL=http://127.0.0.1:8082/v1,http://127.0.0.1:8083/v1 ./bench-phase.sh load64 w4-27b-x2b
./bench-phase.sh scouts w4-27b-tp2b 32:64000:2:off:80:1-3 16:64000:2:off:80:1-3 32:64000:2:on:80:1-1
echo "=== $(date -Is) chain-next7: 8 scouts of the 27B again, and with rolling context"
./bench-phase.sh scouts w4-27b-tp2 8:96000:0:off:100:2-3 8:64000:2:off:100:1-3 8:64000:2:on:100:1-1
echo "=== $(date -Is) chain-next7: the 5060 Ti's own swarm, the 4B and the 9B in 8 bits"
sudo systemctl stop matcher-5060ti matcher-roma-5060ti
./bench-phase.sh load64 q4b-5060   # its 64k sweep in chain-next4 failed as x2b's did
./bench-phase.sh scouts q4b-5060 16:64000:2:off:80:1-1
./bench-phase.sh swarmload q9b-5060
./bench-phase.sh scouts q9b-5060 16:64000:2:off:80:1-1
echo "=== $(date -Is) chain-next7: main and swarm at once"
./bench-phase.sh combo combo-tp2-q4b
sudo systemctl start matcher-5060ti matcher-roma-5060ti
echo "=== $(date -Is) chain-next7: the 27B checks every swarm's reports"
./bench-phase.sh verify w4-27b-tp2
echo "=== $(date -Is) chain-next7: DONE"
