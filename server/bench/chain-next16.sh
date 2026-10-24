#!/bin/bash
# chain-next16 (10-22): the swarms with their areas apart from how many scouts run at once. The a3b-fp8 grid's engine
# log showed 100 scouts at once re-reading nearly every conversation in full each turn (prefix hits 1-8%, 113 minutes
# of prefill for 0.6M tokens generated; see pool_fit in bench-phase.sh), and the grid's 100 against 32 changed area
# size and concurrency together. So every swarm runs the 100 areas with only as many scouts at once as its pool holds,
# three times, and once thinking; rolling (lost on the 4B, the 8-bit A3B and the 4-bit A3B) is dropped; the 27B's
# large scouts get 16 areas 8 at a time beside the 8 that read only part of theirs, and main runs a swarm's job itself.
# Takes over from chain-next15 once a3b-awq's first 100-at-once run (the comparison) ends.
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
[ -n "$3" ] || { echo "chain-next16: needs chain-next15's pid, its bench-phase's and the running scout.py's"; exit 1; }
kill -STOP "$1" "$2"
while ps -o stat= -p "$3" | grep -qv Z; do sleep 30; done
kill -KILL "$2" "$1"
echo "=== $(date -Is) chain-next16: took over from chain-next15 after a3b-awq's 100-at-once run"
python3 scout.py grade --run results/a3b-awq.scout-100-64k-v2.json --manifest scout/manifest.json >> results/phase-scouts.log 2>&1
./bench-arms.sh stop
echo "=== $(date -Is) chain-next16: 100 areas, as many scouts at once as the pool holds"
./bench-phase.sh scouts a3b-awq 100:64000:0:off:40:1-3:auto 100:64000:0:on:40:1-1:auto 200:64000:0:off:40:1-1:auto
./bench-phase.sh scouts q9b-bf16 100:64000:0:off:40:1-1 100:64000:0:off:40:1-3:auto 100:64000:0:on:40:1-1:auto
./bench-phase.sh scouts q4b-bf16-dp 100:64000:0:off:40:1-2:auto
./bench-phase.sh scouts a3b-fp8 100:64000:0:off:40:1-2:auto 100:64000:0:on:40:1-1:auto
echo "=== $(date -Is) chain-next16: the 4-bit 27B as a swarm"
./bench-phase.sh load64 w4-27b-tp2b
SWARM_URL=http://127.0.0.1:8082/v1,http://127.0.0.1:8083/v1 ./bench-phase.sh load64 w4-27b-x2b
./bench-phase.sh scouts w4-27b-tp2b 100:64000:0:off:40:1-3:auto 100:64000:0:on:40:1-1:auto
echo "=== $(date -Is) chain-next16: the 27B's large scouts on main's setup, and main running a swarm's job"
./bench-phase.sh scouts w4-27b-tp2m1r4080 8:96000:0:off:100:1-3 8:96000:0:on:100:1-3 16:96000:0:off:100:1-1:8 \
  16:96000:0:on:100:1-1:8 100:64000:0:off:40:1-1:16
echo "=== $(date -Is) chain-next16: the 5060 Ti's own swarm"; sudo systemctl stop matcher-5060ti matcher-roma-5060ti
./bench-phase.sh load64 q4b-5060; ./bench-phase.sh scouts q4b-5060 100:64000:0:off:40:1-1:auto
./bench-phase.sh swarmload q9b-5060; ./bench-phase.sh scouts q9b-5060 100:64000:0:off:40:1-1:auto
echo "=== $(date -Is) chain-next16: main and swarm at once"; ./bench-phase.sh combo combo-main-q4b
sudo systemctl start matcher-5060ti matcher-roma-5060ti
echo "=== $(date -Is) chain-next16: the 27B (main) checks every swarm's reports"; ./bench-phase.sh verify w4-27b-tp2m1r4080
echo "=== $(date -Is) chain-next16: DONE"
