#!/usr/bin/env bash
# bench-phase.sh <1|2|4> | 3|hard|realwork|redo|speed|arms <arm:engine>... | swarm|swarmload <arm>... | few <arm> <n> <cap> | scouts <arm> <spec>... | combo|verify <arm>: one phase of the 2026-10-17 bench, unattended. For each arm: start it (bench-arms.sh, which
# times the switch), one-request speed (bench-load.py single), 1/3/6 requests at once (bench-load.py load), long-context
# recall (longctx.py), then the next arm. A failed start is logged and skipped. Log: results/phase-<n>.log.
#   1: config 2, one model across both 3090s (5060 Ti untouched)
#   2: config 1, the largest model across all three cards (stops the 5060 Ti's image matchers, starts them after)
#   3: quality of the arms named (e.g. 3 fp8-27b:vllm fn-iq3s-2:strata): the private 14-task set, 3 tries per
#      task, 3 at once, production settings and a 16k thinking cap ended with the wrap-up sentence on both engines
#      (vLLM: thinking_token_budget + the server's reasoning config; Strata: reasoning_budget_tokens); then the hard
#      long-context set (longctx.py build-hard)
#   arms: phase 1's tests on the arms named (a rerun after a failed start); log results/phase-arms.log
#   redo: answers of earlier coding runs that hit the 24k-token cap, asked again at 32k (production's limit)
#   speed: phase 1's tests without the long-context lookups (aced by every arm), for placement variants that keep
#      the weights; log results/phase-speed.log
#      (LEVELS=1,3,6,8,12,16: more agents at once; SINGLE=0: no one-request runs; CAP=128000: longer conversations)
#   swarm <arm>...: phase 4 on the arms named; swarmload <arm>...: its agents-vs-speed sweeps only (a card's
#      capacity for a model already graded); load64 <arm>...: the full-64k sweep only (arms swept before 10-19);
#      few <arm> <scouts> <cap>: the scout swarm with a few large scouts;
#      scouts <arm> <n:cap:rolling:think:turns:reps>...: repeated scout runs; combo <arm>: main and swarm at once;
#      verify <arm>: that arm checks every scout run's reports (the swarm's second tier)
#   hard: 24 hard AtCoder problems from LiveCodeBench v6 on the arms named, one try each, as phase 3 otherwise; log
#      results/phase-hard.log (the held-out set could not tell the 8-bit 27B from IQ3_S, 10-18)
#   realwork: decode speed on real answers, 4 held-out tasks one at a time from an empty page cache, on the arms named
#      (the speed test repeats its prompts and flattered Q4 by 2.6x, 10-19), then one cold 50k prompt, with what the
#      drive read during each and the RAM and swap left; log results/phase-realwork.log
#   codeprofile [arm] [file]: a fn-q4kxl-2z-*s or fn-iq3s-2z-*s arm learns an expert ranking from coding prompts
#      (expert-profile.py) and saves it to /data/<file> for the c arms; results/codeprofile.ok when saved after the
#      prompts; log results/phase-codeprofile.log
#   4: swarm, small models on both 3090s: agents vs speed (bench-load.py load, no thinking) and long-context recall;
#      through 10-19 also real scout swarms (scout.py, run here on the box: read-only tools, no code execution) of
#      16/32/64/100 scouts on the planted-bug target, graded against the manifest (kept outside the folder the scouts
#      can read); those are the scouts grid now
# The production 27B servers must already be stopped.
set -uo pipefail
cd "$(dirname "$0")"
URL=${BENCH_URL:-http://127.0.0.1:8093/v1}   # BENCH_URL / SWARM_URL: a comma list for a two-server arm (bench-load.py splits the agents)
exec >> "results/phase-$1.log" 2>&1

run_arm() {   # run_arm <arm> <vllm|strata> [nolong]
  local arm=$1 engine=$2 nolong=${3:-} model=bench
  [ "$engine" = strata ] && model=x
  # agents start mid-conversation (bench-load.py --pregrow, 10-18), so the KV cache carries its working load inside the
  # 3-minute window. Every arm: at first only the 27B arms did, and Strata's agents, starting fresh on 15k prompts
  # instead of 15-37k ones, would have paid less prefill per turn than the 27B's in the same comparison
  # CAP (10-19): conversations up to that many tokens (bench-load.py's default 40k before). The owner: "16k context is
  # useless, so is 32k. I would say 64k is the minimum", and coders' sessions run past it (production served 150k),
  # so the main candidates are measured again at 128k, where a few agents fill the KV pool
  local grow=--pregrow
  echo "=== $(date -Is) $arm: start"
  ./bench-arms.sh start "$arm" 8093 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  [ "${SINGLE:-1}" = 0 ] || python3 bench-load.py single --url $URL --model $model --label "$arm" --reps 2 --max-tokens 1500
  python3 bench-load.py load --url $URL --model $model --label "$arm" --levels "${LEVELS:-1,3,6}" --cap "${CAP:-40000}" \
    --window 180 --warmup 45 $grow
  [ -n "$nolong" ] || python3 longctx.py run --set longctx-frontpage.json --url $URL --model $model --engine "$engine" --label "$arm"
  echo "=== $(date -Is) $arm: done"
}

swarm_load() {   # swarm_load <arm> <url> [64k]: agents vs speed on the swarm's short prompts (3k shared, 4k own)
  # Without --pregrow each conversation starts at 7k and grows one 800-token answer a turn, so in a 2.5-minute window
  # it stays near 7-11k whatever --cap says: before 10-19 the 16k, 32k and 64k sweeps matched within a few tok/s for
  # that reason, and none of them loaded the KV pool. The 64k sweep starts each conversation part-way to the cap
  # (bench-load.py --pregrow), as a swarm's agents spread through their runs would be. 64k is the owner's floor for
  # real work (10-19); the short sweep stays for short jobs and so new arms compare with the old.
  [ "${3:-}" = 64k ] || python3 bench-load.py load --url "$2" --model bench --label "$1" --think off --max-tokens 800 \
    --cap 32000 --shared 3000 --own 4000 --levels 1,4,16,32,48,64,96,128 --window 120 --warmup 30 --stop-below 10
  python3 bench-load.py load --url "$2" --model bench --label "$1" --think off --max-tokens 800 --cap 64000 --pregrow \
    --shared 3000 --own 4000 --levels 1,4,8,16,24,32,48,64 --window 120 --warmup 30 --stop-below 10
}

swarm_arm() {   # swarm_arm <arm> [loadonly|64k]
  # The scout runs are in the grid since 10-19 (scouts, chain-next7: 64k, rules 2). Through 10-19 this battery also ran
  # rules-1 scouts at 16/32/64/100 x 32k, 100 x 16k and 64k, and 32 thinking (git history): 16k and 32k are under the
  # owner's floor, and under rules 1 the scouts that read in batches hit the cap before reporting.
  local arm=$1 mode=${2:-}
  echo "=== $(date -Is) $arm: start"
  ./bench-arms.sh start "$arm" 8082 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  local U=${SWARM_URL:-http://127.0.0.1:8082/v1}
  swarm_load "$arm" "$U" "$mode"
  [ -n "$mode" ] && { echo "=== $(date -Is) $arm: done"; return; }
  # how much of a 32k or 56k prompt a small model can use, and a weak reference for the main models' scores on the
  # same questions (a test the small models ace as well as the big ones ranks nothing)
  python3 longctx.py run --set longhard-frontpage.json --url $U --model bench --engine vllm --label "$arm" --only-depth 32000,56000
  echo "=== $(date -Is) $arm: done"
}

capped() {   # capped <gen file>: has answers that stopped at a cap below 32k
  [ -f "$1" ] && python3 -c 'import json, sys
sys.exit(0 if any(r["finish"] == "length" and r.get("max_tokens", 24000) < 32768 for r in map(json.loads, open(sys.argv[1]))) else 1)' "$1"
}

realwork_arm() {   # realwork_arm <arm> <vllm|strata>: decode speed on real answers, the same 4 tasks on every arm
  # Why (10-19): the speed test sends the same few prompts again and again, so the experts they use stay in the page
  # cache. Q4 (a 104 GB file through 32 GB of RAM) decoded 35 tok/s there and a median 13.4 on the held-out set's
  # answers, while the box read ~300 MB/s from the NVMe drive and the cards sat mostly idle. lcb/heldout-real.jsonl:
  # 2 algorithmic and 2 practical tasks of the held-out set (private, on the box only), ~17k tokens each on Q4.
  local arm=$1 engine=$2 model=bench field=thinking_token_budget
  [ "$engine" = strata ] && { model=x; field=reasoning_budget_tokens; }
  echo "=== $(date -Is) $arm: realwork start"
  ./bench-arms.sh stop
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  ./bench-arms.sh start "$arm" 8093 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  local r0 w0 r1; r0=$(nvme_read); w0=$(swap_ins)
  echo "=== $(date -Is) $arm: started with $(mem_now)"
  LCB_DATA=lcb/heldout-real.jsonl LCB_TIMEOUT=20 python3 lcb-quick.py gen --url $URL --model $model --label "$arm-real" \
    --attempts 1 --parallel 1 --budget-field $field
  LCB_DATA=lcb/heldout-real.jsonl LCB_TIMEOUT=20 python3 lcb-quick.py grade --label "$arm-real"
  # what the answers read from the NVMe drive (10-20, the owner's goal: none): the drive's own count over the answers,
  # and the engine's per request with its tiers (the log is saved when the arm stops). The RAM line: -2hr ran at 0.2 GiB
  # available with 3 GB of the engine in swap, so a placement that buys drive reads with swap reads shows here
  echo "=== $(date -Is) $arm: the drive read $(( ($(nvme_read) - r0) / 1000000000 )) GB during the answers;" \
    "$(( ($(swap_ins) - w0) * 4096 / 1000000 )) MB came back from swap; now $(mem_now)"
  # one cold 50k prompt (10-20): the owner's work runs 64k+ conversations, and a long prompt touches nearly every
  # expert (on -2h one 50k prompt read 247 GB from the drive); bench-load's short and 50k rows, cold and cached
  r1=$(nvme_read)
  python3 bench-load.py single --url $URL --model $model --label "$arm-real" --reps 1 --max-tokens 800 --sizes 50000
  echo "=== $(date -Is) $arm: the drive read $(( ($(nvme_read) - r1) / 1000000000 )) GB during the short and 50k prompts"
  ./bench-arms.sh stop
  python3 expert-profile.py io "results/$arm.engine.log"
  echo "=== $(date -Is) $arm: realwork done"
}
nvme_read() { awk '$3 == "nvme0n1" { printf "%.0f\n", $6 * 512 }' /proc/diskstats; }   # bytes read since boot (the models' drive)
swap_ins() { awk '$1 == "pswpin" { print $2 }' /proc/vmstat; }                            # pages read back from swap since boot
mem_now() { awk '/^MemAvailable:/ { a = $2 } /^SwapTotal:/ { t = $2 } /^SwapFree:/ { f = $2 }
  END { printf "%.1f GiB available, %.1f GiB in swap", a / 1048576, (t - f) / 1048576 }' /proc/meminfo; }

codeprofile_arm() {   # codeprofile_arm [arm] [file]: an arm that saves its tier learns a coding order (bench-arms.sh, fn-*-2z-*s)
  # The server saves what its tier learned between requests, every 5 minutes (--expert-profile-save-every 5): after the
  # prompts it idles past one save and gets one more short request, and the file must be newer than the prompts' end,
  # or the c arm would start from a stale ranking (or none). A copy goes to results/ for the record.
  # Each quant's arms save to their own file (10-21: IQ3_S's to expert-profile-code-iq3s.bin), the name in bench-arms.sh
  local arm=${1:-fn-q4kxl-2z-kds} pf=${2:-expert-profile-code.bin} U=http://127.0.0.1:8093/v1 t_end saved
  local img; img=$(sed -n 's/^STRATA=//p' bench-arms.sh)
  echo "=== $(date -Is) $arm: codeprofile start"
  rm -f results/codeprofile.ok
  ./bench-arms.sh stop
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  ./bench-arms.sh start "$arm" 8093 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  [ -f profile/code-corpus.json ] || python3 expert-profile.py corpus --out profile/code-corpus.json
  python3 expert-profile.py warm --url $U --corpus profile/code-corpus.json
  t_end=$(date +%s)
  sleep 330
  curl -s -m 600 $U/chat/completions -H 'Content-Type: application/json' \
    -d '{"model": "x", "messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 8, "reasoning_effort": "none"}' >/dev/null
  sleep 90
  saved=$(sudo docker run --rm --entrypoint stat -v bench-strata-data:/data "$img" -c %Y "/data/$pf" 2>/dev/null)
  sudo docker run --rm --entrypoint sh -e PF="$pf" -v bench-strata-data:/data -v "$PWD:/work" "$img" -c \
    'cp "/data/$PF" "/work/results/$PF" && python3 /work/expert-profile.py compare /opt/strata/data/expert-profile.bin "/data/$PF" && python3 /work/expert-profile.py compare /opt/strata/data/expert-profile-sycl.bin "/data/$PF"'
  if [ -n "$saved" ] && [ "$saved" -gt "$t_end" ]; then
    touch results/codeprofile.ok; echo "=== $(date -Is) $arm: profile saved $(( saved - t_end )) s after the prompts"
  else
    echo "=== $(date -Is) $arm: FAILED: no profile saved after the prompts (saved=${saved:-none}, prompts ended $t_end)"
  fi
  echo "=== $(date -Is) $arm: codeprofile done"
}

redo_capped() {   # redo_capped <label> <model> <budget field> [env K=V...]: answers that hit the 24k-token cap asked again
  # at 32k (production's per-answer limit, and the model-choice test's), same seed, so the result is as if run at 32k
  # (10-18: an 8-bit 27B hard answer stopped at 24,000 mid-answer)
  local label=$1 model=$2 field=$3; shift 3
  capped "results/$label.gen.jsonl" || return 0
  env "$@" python3 lcb-quick.py gen --redo-length --max-tokens 32768 --url $URL --model $model --label "$label" --budget-field $field
  env "$@" python3 lcb-quick.py grade --label "$label"
}

redo_arm() {   # redo_arm <arm> <vllm|strata>: redo_capped for the held-out and hard runs an arm already has
  local arm=$1 engine=$2 model=bench field=thinking_token_budget
  [ "$engine" = strata ] && { model=x; field=reasoning_budget_tokens; }
  capped "results/$arm.gen.jsonl" || capped "results/$arm-lcbhard.gen.jsonl" || return 0
  echo "=== $(date -Is) $arm: redo start"
  ./bench-arms.sh start "$arm" 8093 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  redo_capped "$arm" $model $field LCB_DATA=lcb/heldout.jsonl LCB_TIMEOUT=20
  redo_capped "$arm-lcbhard" $model $field LCB_DATA=lcb/test6.jsonl LCB_N_HARD=24 LCB_N_MED=0
  echo "=== $(date -Is) $arm: redo done"
}

quality_arm() {   # quality_arm <arm> <vllm|strata>
  local arm=$1 engine=$2 model=bench field=thinking_token_budget
  [ "$engine" = strata ] && { model=x; field=reasoning_budget_tokens; }
  echo "=== $(date -Is) $arm: quality start"
  ./bench-arms.sh start "$arm" 8093 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  LCB_DATA=lcb/heldout.jsonl LCB_TIMEOUT=20 python3 lcb-quick.py gen --url $URL --model $model --label "$arm" \
    --attempts 3 --parallel 3 --budget-field $field
  LCB_DATA=lcb/heldout.jsonl LCB_TIMEOUT=20 python3 lcb-quick.py grade --label "$arm"
  redo_capped "$arm" $model $field LCB_DATA=lcb/heldout.jsonl LCB_TIMEOUT=20
  # the long-context questions that need the whole prompt (the lookups were aced by every arm, 10-18)
  python3 longctx.py run --set longhard-frontpage.json --url $URL --model $model --engine "$engine" --label "$arm"
  echo "=== $(date -Is) $arm: quality done"
}

hard_arm() {   # hard_arm <arm> <vllm|strata>: public and possibly seen in training, so second to the held-out set
  local arm=$1 engine=$2 model=bench field=thinking_token_budget
  [ "$engine" = strata ] && { model=x; field=reasoning_budget_tokens; }
  echo "=== $(date -Is) $arm: hard start"
  ./bench-arms.sh start "$arm" 8093 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  # HARD_ATTEMPTS=3 (10-20, the owner: "lets just wait to see if q4 is even worth it"): one try per problem left Q4
  # against IQ3_S unsettled (held-out 6 tasks to 2, p = 0.29); gen keeps the tries a label already has and adds the rest
  LCB_DATA=lcb/test6.jsonl LCB_N_HARD=24 LCB_N_MED=0 python3 lcb-quick.py gen --url $URL --model $model --label "$arm-lcbhard" \
    --attempts "${HARD_ATTEMPTS:-1}" --parallel 3 --budget-field $field
  LCB_DATA=lcb/test6.jsonl LCB_N_HARD=24 LCB_N_MED=0 python3 lcb-quick.py grade --label "$arm-lcbhard"
  redo_capped "$arm-lcbhard" $model $field LCB_DATA=lcb/test6.jsonl LCB_N_HARD=24 LCB_N_MED=0
  echo "=== $(date -Is) $arm: hard done"
}

pool_fit() {   # pool_fit <urls> <n>: how many scouts at once the server's KV pool holds without thrashing, at most n
  # Why (10-22, a3b-fp8 at 100 scouts at once): the engine re-read nearly every conversation in full each turn (prefix
  # hits 1-8%, 33M prompt tokens computed for 0.6M generated, 113 minutes of prefill at ~5,000 tok/s); its 4-bit build
  # had 78 requests waiting with KV use at 19% (that figure leaves out cached blocks no request holds). A miss
  # re-prefills the whole conversation into new blocks, which pushes the whole pool through LRU in a few minutes, and a
  # scout's next turn came about 4 minutes later, after its cached prefix was gone: misses beget misses. The 27B's 8 scouts, whose 8 x 91k fit its 831k pool, made 33 turns each in 18 minutes. So: as many at once
  # as 85% of the pool holds at 40k a scout (about the 90th percentile of a 100-area scout's final context at 64k).
  local u p pool=0
  for u in ${1//,/ }; do
    p=$(curl -s --max-time 10 "${u%/v1}/metrics" | python3 -c 'import re, sys
print(sum(int(float(v)) for v in re.findall(r"^vllm:cache_config_info\{[^}]*kv_cache_size_tokens=\"([0-9.]+)\"", sys.stdin.read(), re.M)))')
    pool=$((pool + ${p:-0}))
  done
  if [ "$pool" -le 0 ]; then echo "pool_fit: no KV pool size from $1, running all $2 at once" >&2; echo "$2"; return; fi
  p=$(python3 -c "print(max(2, min($2, int(0.85 * $pool / 40000))))")
  echo "pool_fit: $pool tokens of KV pool, $p scouts at once" >&2; echo "$p"
}

scouts_arm() {   # scouts_arm <arm> <scouts:cap:rolling:think:turns:reps[:at]>...: scout runs on one arm, e.g. 32:32000:2:off:80:1-3
  # Repeats and rolling context (10-18): scouts sample at temperature 0.7, so one run per setting leaves a few bugs of
  # noise; and at 16 and 32 scouts the 4B's scouts filled their 32k with what they had read and stopped with their
  # areas half read (16 scouts: 14 at the cap, 2 of 60 found), which measures the cap, not the model. Rolling N keeps
  # only the last N file reads in a scout's context. All under scout.py's rules 2 (10-19: the brief says to report as
  # you go, and a read that would overfill the context is refused; see RULES2 there), named -v2 so they never pass for
  # repeats of the battery's runs, which keep rules 1. Rep 1 has no -r suffix.
  # An optional 7th field runs the n areas only that many scouts at a time (a number, or auto: pool_fit), named -atN.
  local arm=$1 spec n cap roll think turns reps at i out; shift
  echo "=== $(date -Is) $arm: start"
  ./bench-arms.sh start "$arm" 8082 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  local U=${SWARM_URL:-http://127.0.0.1:8082/v1}
  for spec in "$@"; do
    IFS=: read -r n cap roll think turns reps at <<< "$spec"
    at=${at:-$n}; [ "$at" = auto ] && at=$(pool_fit "$U" "$n")
    [ -f "scout/assign/a$n.json" ] || python3 scout.py split --target scout/target --agents "$n" --out "scout/assign/a$n.json"
    for i in $(seq "${reps%-*}" "${reps#*-}"); do
      out="results/$arm.scout-$n-$((cap / 1000))k-v2"
      [ "$at" != "$n" ] && out="$out-at$at"
      [ "$roll" != 0 ] && out="$out-roll$roll"
      [ "$think" = on ] && out="$out-think"
      [ "$i" != 1 ] && out="$out-r$i"
      python3 scout.py run --url $U --target scout/target --assign "scout/assign/a$n.json" --agents "$at" --cap "$cap" \
        --rolling "$roll" --max-turns "$turns" --thinking "$think" --rules 2 --out "$out.json" > "$out.log" 2>&1
      python3 scout.py grade --run "$out.json" --manifest scout/manifest.json
    done
  done
  echo "=== $(date -Is) $arm: done"
}

few_arm() {   # few_arm <arm> <scouts> <cap>: a few large scouts instead of many small ones, thinking off and on
  # The owner's question (10-18): 8 agents of the 27B, its best load point, against a hundred small ones. 8 scouts
  # take ~5,500 of the target's 44k lines each: past the swarm's 32k cap and 40 turns (150 lines a read), so these get
  # the cap given and 100 turns. 8 x 96k fits the 4-bit 27B's 831k-token pool. The estimate of ~60k tokens a slice was
  # low: read with line numbers and tool turns, a line costs ~21 tokens, and the first run's scouts all stopped near
  # 91k with 73-78% of their slices read (10-20), so chain-next7 adds 8 scouts with rolling context.
  scouts_arm "$1" "$2:$3:0:off:100:1-1" "$2:$3:0:on:100:1-1"
}

combo_arm() {   # combo_arm <arm>: a two-server arm's main (port 8093) and swarm (8094) loaded at the same time
  # The service's plan (10-18): the 27B serving work orders on the 3090s while a small model on the 5060 Ti swarms.
  # Each side's numbers here against its own alone shows what running both costs. The swarm side's window covers the
  # main side's warm-up and window, so the two overlap the whole time the main side is measured.
  local arm=$1 n A=http://127.0.0.1:8093/v1 B=http://127.0.0.1:8094/v1
  echo "=== $(date -Is) $arm: start"
  ./bench-arms.sh start "$arm" 8093 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  # 10-19: main at 128k and the swarm at 64k, as the arms alone are measured (the 5060 Ti's pool holds a few full 64k
  # conversations, so 8 and 16 instead of 16 and 32)
  for n in 8 16; do
    python3 bench-load.py load --url $A --model bench --label "$arm-main" --levels 8 --cap 128000 --window 180 --warmup 45 \
      --pregrow &
    python3 bench-load.py load --url $B --model bench --label "$arm-swarm" --think off --max-tokens 800 --cap 64000 \
      --pregrow --shared 3000 --own 4000 --levels $n --window 600 --warmup 30 &
    wait
  done
  # 10-22: the swarm side as a swarm runs now (100 areas, as many at once as the 5060 Ti's pool holds, no rolling: see
  # pool_fit), and main under load in 10-minute windows back to back for as long as the swarm runs, so all of the
  # swarm's time is spent beside a busy main
  local at sp out; at=$(pool_fit $B 100); out="results/$arm.scout-100-64k-v2-at$at"
  python3 scout.py run --url $B --target scout/target --assign scout/assign/a100.json --agents "$at" --cap 64000 \
    --rolling 0 --max-turns 40 --rules 2 --out "$out.json" > "$out.log" 2>&1 &
  sp=$!
  while kill -0 $sp 2>/dev/null; do
    python3 bench-load.py load --url $A --model bench --label "$arm-main-scouts" --levels 8 --cap 128000 --window 600 \
      --warmup 45 --pregrow
  done
  wait $sp
  python3 scout.py grade --run "$out.json" --manifest scout/manifest.json
  echo "=== $(date -Is) $arm: done"
}

verify_all() {   # verify_all <arm>: the swarm's second tier over every scout run so far (scout.py verify, verdicts cached)
  # The design the service would use (10-18): small scouts report widely, the 27B checks each reported line, and only
  # what it calls a bug goes on. Graded as a run: planted bugs kept against other reports dropped.
  local arm=$1 f
  echo "=== $(date -Is) $arm: start"
  ./bench-arms.sh start "$arm" 8082 || { echo "=== $(date -Is) $arm: FAILED to start, skipped"; return; }
  # And (10-19) whether each report within 3 lines of a planted bug names it: the 4B's 21 hits at 100 scouts held 3
  # reports of something else nearby. Every run is graded again with those verdicts (found_named in its grade).
  for f in results/*.scout-*.json; do
    case $f in *.verified.json|*.grade.json) continue ;; esac
    python3 scout.py match --run "$f" --manifest scout/manifest.json --url http://127.0.0.1:8082/v1 \
      --cache results/match-cache.json --thinking on --parallel 16
    python3 scout.py grade --run "$f" --manifest scout/manifest.json --match results/match-cache.json
    [ -f "${f%.json}.verified.json" ] || python3 scout.py verify --run "$f" --url http://127.0.0.1:8082/v1 \
      --out "${f%.json}.verified.json" --cache results/verify-cache.json --thinking on --parallel 16 --label "$arm"
    python3 scout.py grade --run "${f%.json}.verified.json" --manifest scout/manifest.json --match results/match-cache.json
  done
  echo "=== $(date -Is) $arm: done"
}

case ${1:-} in
  few) shift; few_arm "$@"
     set -- few ;;
  scouts) shift; scouts_arm "$@"
     set -- scouts ;;
  combo) shift; combo_arm "$1"
     set -- combo ;;
  verify) shift; verify_all "$1"
     set -- verify ;;
  arms) shift; for spec in "$@"; do run_arm "${spec%%:*}" "${spec##*:}"; done
     set -- arms ;;
  swarm) shift; for arm in "$@"; do swarm_arm "$arm"; done
     set -- swarm ;;
  swarmload) shift; for arm in "$@"; do swarm_arm "$arm" loadonly; done
     set -- swarmload ;;
  load64) shift; for arm in "$@"; do swarm_arm "$arm" 64k; done
     set -- load64 ;;
  speed) shift; for spec in "$@"; do run_arm "${spec%%:*}" "${spec##*:}" nolong; done
     set -- speed ;;
  redo) shift; for spec in "$@"; do redo_arm "${spec%%:*}" "${spec##*:}"; done
     set -- redo ;;
  hard) shift; for spec in "$@"; do hard_arm "${spec%%:*}" "${spec##*:}"; done
     set -- hard ;;
  realwork) shift; for spec in "$@"; do realwork_arm "${spec%%:*}" "${spec##*:}"; done
     set -- realwork ;;
  codeprofile) codeprofile_arm "${2:-}" "${3:-}" ;;
  3) shift; for spec in "$@"; do quality_arm "${spec%%:*}" "${spec##*:}"; done
     set -- 3 ;;
  # q9b-bf16-dp left out (10-18): the 9B's 18 GB of weights on each card leave ~4 GB of KV per card, a fifth of
  # the tensor-parallel pool, too little for many 32k scouts
  4) swarm_arm a3b-awq
     swarm_arm q9b-bf16
     swarm_arm q4b-bf16-dp
     swarm_arm a3b-fp8 ;;
  1) run_arm fp8-27b vllm
     run_arm fn-iq2xs-2 strata
     run_arm fn-iq3s-2 strata
     run_arm fn-q4kxl-2 strata ;;
  2) sudo systemctl stop matcher-5060ti matcher-roma-5060ti
     run_arm fn-q4kxl-3 strata
     run_arm fn-iq3s-3 strata
     ./bench-arms.sh stop
     sudo systemctl start matcher-5060ti matcher-roma-5060ti ;;
  *) sed -n 2,33p "$0"; exit 1 ;;
esac
./bench-arms.sh stop
echo "=== $(date -Is) phase $1 finished"
