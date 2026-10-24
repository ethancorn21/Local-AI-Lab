#!/bin/bash
# Pauses chain-next7 between two of its steps again (10-21). chain-next12 found main's fix at 128k: with one checkpoint
# per five blocks instead of every block, -tp2m1 held 8 agents (280 tok/s, 98% of prompt tokens from the cache) where
# it had stalled at 6. Before it is main's setup it runs the 40k test too, where -tp2m1 with dense checkpoints reached
# 16 agents (490 tok/s): a sparse checkpoint makes a returning turn read up to 4,080 tokens again, and at 16 agents
# that must not cost the scaling. Then chain-next7 goes on.
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next13.sh <chain-next7 pid> <its bench-phase pid> &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
[ -n "$2" ] || { echo "chain-next13: needs chain-next7's pid and the pid of the bench-phase it runs"; exit 1; }
kill -STOP "$1"
while ps -o stat= -p "$2" | grep -qv Z; do sleep 30; done
echo "=== $(date -Is) chain-next13: main's fix at 40k conversations (chain-next7 paused)"
./bench-arms.sh stop
cp results/w4-27b-tp2m1r4080.engine.log results/w4-27b-tp2m1r4080-128k.engine.log
SINGLE=0 LEVELS=1,3,6,8,12,16 ./bench-phase.sh speed w4-27b-tp2m1r4080:vllm
echo "=== $(date -Is) chain-next13: DONE, chain-next7 goes on"
kill -CONT "$1"
