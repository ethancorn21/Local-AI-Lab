#!/bin/bash
# Takes over from chain-next14 (10-22) once its running scout run ends (a3b-awq, 100 scouts rolling, the first of its
# grid). Two holes the first two grids showed:
#  - The grid spent its three repeats on rolling context, and rolling lost on both models done: the 4B found 17-19
#    rolling against 26 keeping its reads, the A3B 25-28 against 42, and rolling runs take longer (the A3B 176-180 min
#    against 113: a dropped read changes the prompt mid-conversation, so the rest is read again). The best setting had
#    one run each. Here it gets three on every small model, and rolling one run on the models still to do.
#  - The 27B's 8 big scouts ran on w4-27b-tp2, MTP with dense prefix-cache checkpoints, which stall past 6 long
#    conversations (chain-next12). Main is w4-27b-tp2m1r4080 now; its 8 scouts run there, thinking off and on three
#    times each (the first pair, 13 and 19 found, was the cleanest per hour of any swarm), rolling once each.
#    The 27B as a swarm stays on -tp2b (batch mode: no drafter, so no dense checkpoints). The 27B's checks run on main.
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next15.sh <chain-next14 pid> <its bench-phase pid> <scout.py pid> &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
[ -n "$3" ] || { echo "chain-next15: needs chain-next14's pid, its bench-phase's and the running scout.py's"; exit 1; }
kill -STOP "$1" "$2"   # neither goes on to the next run; the running scout run finishes
while ps -o stat= -p "$3" | grep -qv Z; do sleep 30; done
kill -KILL "$2" "$1"
echo "=== $(date -Is) chain-next15: took over from chain-next14 after a3b-awq's first 100-scout rolling run"
python3 scout.py grade --run results/a3b-awq.scout-100-64k-v2-roll2.json --manifest scout/manifest.json \
  >> results/phase-scouts.log 2>&1
./bench-arms.sh stop
echo "=== $(date -Is) chain-next15: the small swarms' best setting three times, rolling once"
./bench-phase.sh scouts a3b-awq 100:64000:0:off:40:1-3 32:64000:2:off:80:1-1 32:64000:2:on:80:1-1
./bench-phase.sh scouts q9b-bf16 100:64000:0:off:40:1-3 100:64000:2:off:80:1-1 32:64000:2:off:80:1-1 32:64000:2:on:80:1-1
./bench-phase.sh scouts q4b-bf16-dp 100:64000:0:off:40:2-3
./bench-phase.sh scouts a3b-fp8 100:64000:0:off:40:2-3
echo "=== $(date -Is) chain-next15: the 4-bit 27B as a swarm at 64k, 32 and 16 scouts, and 32 thinking"
./bench-phase.sh load64 w4-27b-tp2b
SWARM_URL=http://127.0.0.1:8082/v1,http://127.0.0.1:8083/v1 ./bench-phase.sh load64 w4-27b-x2b
./bench-phase.sh scouts w4-27b-tp2b 32:64000:2:off:80:1-3 16:64000:2:off:80:1-3 32:64000:2:on:80:1-1
echo "=== $(date -Is) chain-next15: 8 scouts of the 27B on main's setup"
./bench-phase.sh scouts w4-27b-tp2m1r4080 8:96000:0:off:100:1-3 8:96000:0:on:100:1-3 8:64000:2:off:100:1-1 \
  8:64000:2:on:100:1-1
echo "=== $(date -Is) chain-next15: the 5060 Ti's own swarm, the 4B and the 9B in 8 bits"
sudo systemctl stop matcher-5060ti matcher-roma-5060ti
./bench-phase.sh load64 q4b-5060
./bench-phase.sh scouts q4b-5060 16:64000:2:off:80:1-1
./bench-phase.sh swarmload q9b-5060
./bench-phase.sh scouts q9b-5060 16:64000:2:off:80:1-1
echo "=== $(date -Is) chain-next15: main and swarm at once"
./bench-phase.sh combo combo-main-q4b
sudo systemctl start matcher-5060ti matcher-roma-5060ti
echo "=== $(date -Is) chain-next15: the 27B (main) checks every swarm's reports"
./bench-phase.sh verify w4-27b-tp2m1r4080
echo "=== $(date -Is) chain-next15: DONE"
