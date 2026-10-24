#!/bin/bash
# Resumes the queue after the box crashed at 11:05 on 10-21 (journal cut off mid-entry, no shutdown, no hardware error
# logged; off until 16:51; CPU package 85-89 C on average for the hours before, touching 100). It was in chain-next7's
# first swarm step (the 4B's grid, its third 100-scout rolling run) with chain-next13 waiting to slot in main's 40k
# check; both died with it. Here: chain-next13's check, the 4B's grid from that run on, then the rest of chain-next7
# as written there.
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next14.sh &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
echo "=== $(date -Is) chain-next14: resumed after the 11:05 crash; main's fix at 40k conversations"
./bench-arms.sh stop
cp results/w4-27b-tp2m1r4080.engine.log results/w4-27b-tp2m1r4080-128k.engine.log
SINGLE=0 LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed w4-27b-tp2m1r4080:vllm
GRID="100:64000:2:off:80:1-3 32:64000:2:off:80:1-3 100:64000:0:off:40:1-1 32:64000:2:on:80:1-1"
echo "=== $(date -Is) chain-next14: the small swarms on the grid at 64k, from the 4B's third 100-scout run"
./bench-phase.sh scouts q4b-bf16-dp 100:64000:2:off:80:3-3 ${GRID#* }
for a in a3b-fp8 a3b-awq q9b-bf16; do
  case $a in a3b-fp8) ./bench-phase.sh load64 $a ;; esac
  ./bench-phase.sh scouts $a $GRID
done
echo "=== $(date -Is) chain-next14: the 4-bit 27B as a swarm at 64k, 32 and 16 scouts, and 32 thinking"
./bench-phase.sh load64 w4-27b-tp2b
SWARM_URL=http://127.0.0.1:8082/v1,http://127.0.0.1:8083/v1 ./bench-phase.sh load64 w4-27b-x2b
./bench-phase.sh scouts w4-27b-tp2b 32:64000:2:off:80:1-3 16:64000:2:off:80:1-3 32:64000:2:on:80:1-1
echo "=== $(date -Is) chain-next14: 8 scouts of the 27B again, and with rolling context"
./bench-phase.sh scouts w4-27b-tp2 8:96000:0:off:100:2-3 8:64000:2:off:100:1-3 8:64000:2:on:100:1-1
echo "=== $(date -Is) chain-next14: the 5060 Ti's own swarm, the 4B and the 9B in 8 bits"
sudo systemctl stop matcher-5060ti matcher-roma-5060ti
./bench-phase.sh load64 q4b-5060
./bench-phase.sh scouts q4b-5060 16:64000:2:off:80:1-1
./bench-phase.sh swarmload q9b-5060
./bench-phase.sh scouts q9b-5060 16:64000:2:off:80:1-1
echo "=== $(date -Is) chain-next14: main and swarm at once"
./bench-phase.sh combo combo-tp2-q4b
sudo systemctl start matcher-5060ti matcher-roma-5060ti
echo "=== $(date -Is) chain-next14: the 27B checks every swarm's reports"
./bench-phase.sh verify w4-27b-tp2
echo "=== $(date -Is) chain-next14: DONE"
