#!/bin/bash
# Pauses chain-next7 between two of its steps (10-21) for a follow-up on main at 128k conversations. There -tp2 and
# -tp2m1 (MTP, three drafts and one) stalled at 6 agents, first tokens 94-111 s with the pool 70% full, while -tp2n (no
# MTP) held 8 at 29 tok/s each; at 40k -tp2m1 had scaled to 16. The engine log names a likely cause: with MTP, vLLM
# keeps a Mamba state checkpoint at every block for the prefix cache (bench-arms.sh, -tp2r). Here both MTP arms with
# one checkpoint per five blocks, and -tp2m1 as it was between them (a second run of the stall). The load rows now
# carry the window's prefix-cache hit share. Then chain-next7 goes on.
# Restarted 07:02 with the intervals fixed (below); the second start passes the pid of the first one's bench-phase.
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next12.sh <chain-next7 pid> <its bench-phase pid> &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
[ -n "$2" ] || { echo "chain-next12: needs chain-next7's pid and the pid of the bench-phase it runs"; exit 1; }
kill -STOP "$1"   # chain-next7 stops before its next step; the step it is in runs on
# a finished child of a stopped parent stays a zombie until the parent runs again, so wait on its state, not kill -0
while ps -o stat= -p "$2" | grep -qv Z; do sleep 30; done
echo "=== $(date -Is) chain-next12: main at 128k, MTP with sparse prefix-cache checkpoints (chain-next7 paused)"
./bench-arms.sh stop
cp results/w4-27b-tp2m1.engine.log results/w4-27b-tp2m1-128k-1.engine.log   # the rerun's log replaces it
run() { CAP=128000 SINGLE=0 LEVELS=1,2,4,6,8,12 ./bench-phase.sh speed "$1:vllm"; }
# The interval must be a multiple of the engine's block, which it sizes so a KV page holds a Mamba state: 832 tokens
# with three drafts, 816 with one (the first start, at 4,160, was refused). Five blocks each
run w4-27b-tp2m1r4080
run w4-27b-tp2m1
run w4-27b-tp2r4160
echo "=== $(date -Is) chain-next12: DONE, chain-next7 goes on"
kill -CONT "$1"
