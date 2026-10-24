#!/usr/bin/env bash
# bench-arms.sh: start and stop the 2026-10-17 bench's serving setups the same way every time, and time each start.
#
#   bench-arms.sh start <arm> [port]   start one arm (stops any other bench arm first), wait until it answers, and
#                                      write results/<arm>.switch.json: seconds from start to /health and to the
#                                      first token of a tiny request (the "switch time" a scheduler pays)
#   bench-arms.sh stop                 stop every bench arm
#   bench-arms.sh list                 the arms
#
# Every arm listens on 127.0.0.1 only (the AI box has no host firewall), on a port the tailnet does not forward
# (default 8093; the swarm arms use 8082, which the harness VM already tunnels to). The production servers
# (hyperqwen-single-1, hyperqwen-c-single-1) are not touched here: stop them first.
# vLLM arms run the HyperQwen image's own vLLM 0.29 with a plain `vllm serve` (HyperQwen's speed-ups exist only for
# its 4-bit 27B build); PATH carries the venv's bin, or FlashInfer's kernel build finds no ninja (first try, 10-17); Strata arms run strata:477ac13 (Niko1221/Strata at 477ac13, built for sm86 + sm120).
set -euo pipefail
cd "$(dirname "$0")"
M=/opt/llm/engines/models/bench
G3090=GPU-0f9757bb-934e-8f17-6839-ccc72e99f1cb
G3090TI=GPU-56122485-2e24-e358-a731-574234bbc100
G5060=GPU-ccdfa1bc-3062-ad1c-36ad-77e6bcdb7f3a
HQ=ghcr.io/syv-ai/hyperqwen:latest
# strata:477ac13-built = strata:477ac13 plus the engine its setup compiled at the first start (it found no ready-made
# Linux engine and compiled, ~8 min, ignoring the image's own build; committed so later starts skip it, 10-17; again
# for sm_86 + sm_120 when the 5060 Ti joined, 10-18). Commit only with /opt/strata/strata-*.json removed: setup starts
# whichever config it finds there, and two of them make it ask "Which one?" with no terminal to answer (fn-iq3s-3,
# fn-iq3s-2 failed so, 10-18)
STRATA=strata:477ac13-built
# The servers' own wrap-up at the thinking cap, as in production (server/vllm-hyperqwen/env.example)
WRAP='{"reasoning_end_str":"\n\nI have thought about this long enough; time to give my answer.\n</think>\n\n"}'
WRAP_HQ='{"reasoning_end_str":"\n\nI\u0020have\u0020thought\u0020about\u0020this\u0020long\u0020enough;\u0020time\u0020to\u0020give\u0020my\u0020answer.\n</think>\n\n"}'

ARMS="fp8-27b  fp8-27b-mtp  fp8-27b-pp  fp8-27b-pp-mtp  fp8-27b-pp3  fp8-27b-pp3-mtp  w4-27b-tp2  w4-27b-tp2n  w4-27b-tp2m1  w4-27b-tp2r<n>  w4-27b-tp2m1r<n>  w4-27b-x2  w4-27b-tp2b  w4-27b-x2b  fn-iq3s-2-p2  fn-iq3s-2-bm  fn-iq3s-2-b4  fn-iq3s-2s  fn-iq3s-2t  fn-iq3s-2h  fn-q4kxl-2t  fn-q4kxl-2h  fn-q4kxl-2hr  fn-q4kxl-2hrp  fn-q4kxl-2hrpk  fn-q4kxl-2hrpks  fn-q4kxl-2hrpkc  fn-q4kxl-2hrpkcd  fn-q4kxl-2z-<kqdpsc>  fn-iq3s-2z-<hsc><n>  fn-iq2xs-2  fn-iq3s-2  fn-q4kxl-2  fn-q4kxl-3  fn-iq3s-3  a3b-fp8  a3b-awq  q9b-bf16  q9b-bf16-dp  q4b-bf16-dp  q4b-5060  q9b-5060  combo-main-q4b"

vllm_run() {   # vllm_run <name> <port> <gpus> <model dir> <max len> <extra vllm args...>   (VLLM_ENV=K=V: one more env var)
  # CUDA_DEVICE_ORDER: the cards in bus order (3090, 5060 Ti, 3090 Ti), not CUDA's fastest first, so a pipeline's
  # stages land where VLLM_PP_LAYER_PARTITION expects them
  local name=$1 port=$2 gpus=$3 model=$4 len=$5; shift 5
  sudo docker run -d --name "bench-$name" --gpus "\"device=$gpus\"" --ipc host --shm-size 16g \
    -p "127.0.0.1:$port:8000" -v "$M:/models:ro" -v bench-vllm-cache:/cache -e HOME=/cache \
    -e CUDA_DEVICE_ORDER=PCI_BUS_ID ${VLLM_ENV:+-e $VLLM_ENV} \
    -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:False -e FLASHINFER_DISABLE_VERSION_CHECK=1 \
    -e PATH=/app/venv/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    --entrypoint /app/venv/bin/vllm "$HQ" serve "/models/$model" --served-model-name bench \
    --host 0.0.0.0 --port 8000 --max-model-len "$len" --gpu-memory-utilization "${GPU_UTIL:-0.92}" \
    --kv-cache-dtype fp8 --enable-prefix-caching --reasoning-parser qwen3 --reasoning-config "$WRAP" \
    --enable-auto-tool-choice --tool-call-parser qwen3_coder "$@" ${MAX_SEQS:+--max-num-seqs $MAX_SEQS} >/dev/null   # tool calls as in production
}

hq_run() {   # hq_run <name> <port> <gpus> <mode single|batch> <cache a|b> [K=V env...]   (XARGS: more vllm arguments)
  # Production's 4-bit 27B build through HyperQwen's own launcher, as hyperqwen-single-1 runs it (MTP and the fast
  # draft vocabulary in single mode; batch mode: no drafter, int8 activations, made for throughput). Its serve line
  # names the model qwen3.8-27b and then appends EXTRA_ARGS, so a later --served-model-name wins. The wrap-up is
  # production's own string, spaces as   because the launcher splits EXTRA_ARGS on spaces. The compile caches start
  # as copies of production's (a: the 3090 Ti's server, b: the 3090's), so a start is as warm as production's.
  local name=$1 port=$2 gpus=$3 mode=$4 cache=$5 kv e=(); shift 5
  local src=hyperqwen_qwen-cache; [ "$cache" = b ] && src=hyperqwen-c_qwen-cache
  sudo docker volume inspect "bench-hq-cache-$cache" >/dev/null 2>&1 || sudo docker run --rm --entrypoint bash \
    -v "$src:/src:ro" -v "bench-hq-cache-$cache:/dst" "$HQ" -c 'cp -a /src/. /dst/' || return 1
  for kv in "$@"; do e+=(-e "$kv"); done
  sudo docker run -d --name "bench-$name" --gpus "\"device=$gpus\"" --ipc host --shm-size 16g \
    -p "127.0.0.1:$port:8080" -v /opt/llm/engines/models/hyperqwen:/app/models -v "bench-hq-cache-$cache:/cache" \
    -e PORT=8080 -e CTX=long -e SPEC=mtp -e PREFIX_CACHE=1 -e VISION=0 -e PREPARE=0 "${e[@]}" \
    -e EXTRA_ARGS="--served-model-name bench --reasoning-config $WRAP_HQ ${XARGS:-}" "$HQ" "$mode" >/dev/null
}

strata_run() {   # strata_run <name> <port> <gpus> <family> <model> <gguf dir> <context>   (CONFIG=<file on /data>: start that config;
  # GPUS_ORDER=0,2,1: the cards in that order, numbered as nvidia-smi does inside the container (bus order: 3090,
  # 5060 Ti, 3090 Ti); the entry point passes GPUS to setup, which saves it as the config's "gpu", and with a manual
  # --layer-split the engine stages the first cards in that order and gives the rest to the helper caches. Always
  # pass it: the image carries GPUS=0,1,2 from the container it was committed from, so leaving GPUS out (the first
  # NOGPUS=1, 10-18) still ran 0,1,2 and put the layers on the 3090 and the 5060 Ti)
  # The image's entry point takes its settings from env vars only: arguments after the image are dropped (10-18)
  local name=$1 port=$2 gpus=$3 family=$4 model=$5 gguf=$6 ctx=$7 n
  n=$(tr ',' '\n' <<< "$gpus" | wc -l)
  sudo docker run -d --name "bench-$name" --gpus "\"device=$gpus\"" --ulimit memlock=-1 --stop-timeout 60 \
    -p "127.0.0.1:$port:8080" -v "$gguf:/gguf" -v bench-strata-data:/data \
    -e FAMILY="$family" -e MODEL="$model" -e GGUF_DIR=/gguf -e CONTEXT="$ctx" -e VISION=no ${CONFIG:+-e CONFIG=$CONFIG} \
    -e GPUS="${GPUS_ORDER:-$(seq -s, 0 $((n - 1)))}" "$STRATA" >/dev/null
}

strata_cfg() {   # strata_cfg <saved config> <new config> <python on c>: a variant of a config setup saved, on the volume
  sudo docker run --rm --entrypoint python3 -v bench-strata-data:/data "$STRATA" -c "import json
c = json.load(open('/data/config/$1.json'))
$3
json.dump(c, open('/data/config/$2.json', 'w'), indent=1)"
}

start() {
  local arm=$1 port=${2:-8093}
  if ! start_once "$arm" "$port"; then
    # vLLM sizes the hybrid model's recurrent-state slots from free memory; above them it refuses to start and says
    # how many fit (fp8-27b, 10-17: 256 > 194). Retry once at that number.
    local n; n=$(grep -oE "max_num_seqs to at most [0-9]+" "results/$arm.start.log" 2>/dev/null | grep -oE "[0-9]+$" | head -1)
    [ -n "$n" ] || exit 1
    echo "retrying $arm with --max-num-seqs $n"
    MAX_SEQS=$n start_once "$arm" "$port" || exit 1
  fi
}

start_once() {
  local arm=$1 port=${2:-8093} second=""   # second: a two-server arm's other port
  stop_all
  local t0; t0=$(date +%s)
  case $arm in
    # config 2: one model across both 3090s, 5060 Ti free
    fp8-27b)     vllm_run "$arm" "$port" "$G3090,$G3090TI" Qwen3.8-27B-FP8 262144 --tensor-parallel-size 2 --max-num-seqs 16 ;;
    fn-iq2xs-2)  strata_run "$arm" "$port" "$G3090,$G3090TI" qwen IQ2_XS "$M/flash/IQ2_XS/IQ2_XS" 262144 ;;
    fn-iq3s-2)   strata_run "$arm" "$port" "$G3090,$G3090TI" qwen IQ3_S "$M/flash/IQ3_S/IQ3_S" 262144 ;;
    fn-q4kxl-2)  strata_run "$arm" "$port" "$G3090,$G3090TI" unsloth UD-Q4_K_XL "$M/flash/UD-Q4_K_XL/UD-Q4_K_XL" 262144 ;;
    # variants: Qwen's own MTP drafter on the 8-bit 27B (the 4-bit production build has MTP; plain vLLM had none),
    # and Strata decoding two requests together (it serves one at a time by default: 3 agents waited 36 s, 10-18)
    fp8-27b-mtp) vllm_run "$arm" "$port" "$G3090,$G3090TI" Qwen3.8-27B-FP8 262144 --tensor-parallel-size 2 --max-num-seqs 16 \
                   --speculative-config '{"method":"mtp","num_speculative_tokens":3}' ;;
    # Layers instead of tensors (10-18, after the owner caught the same gap on Strata): tensor parallel syncs both cards
    # twice per layer over the 3090 Ti's x4 link; a pipeline passes one activation per token between them. -pp3 adds the
    # 5060 Ti as a middle stage (bus order 3090, 5060 Ti, 3090 Ti) with fewer layers for its half-speed memory, for its
    # 16 GB of KV pool. -pp at 131k: at 262k it did not start (each stage keeps full-width activations and one of the
    # two vocabulary tables, leaving 3.78 GiB of KV against the 4.16 one full-length request needs, 10-18); the same
    # 0.92 memory share as tensor parallel, so the pools compare. The -mtp pipelines cannot run: vLLM 0.29's MTP drafter
    # does not support pipeline parallelism ("does not implement SupportsPP")
    fp8-27b-pp)  vllm_run "$arm" "$port" "$G3090,$G3090TI" Qwen3.8-27B-FP8 131072 --pipeline-parallel-size 2 --max-num-seqs 16 ;;
    fp8-27b-pp-mtp) vllm_run "$arm" "$port" "$G3090,$G3090TI" Qwen3.8-27B-FP8 262144 --pipeline-parallel-size 2 --max-num-seqs 16 \
                   --speculative-config '{"method":"mtp","num_speculative_tokens":3}' ;;
    fp8-27b-pp3) VLLM_ENV=VLLM_PP_LAYER_PARTITION=26,12,26 vllm_run "$arm" "$port" "$G3090,$G3090TI,$G5060" Qwen3.8-27B-FP8 262144 \
                   --pipeline-parallel-size 3 --max-num-seqs 16 ;;
    fp8-27b-pp3-mtp) VLLM_ENV=VLLM_PP_LAYER_PARTITION=26,12,26 vllm_run "$arm" "$port" "$G3090,$G3090TI,$G5060" Qwen3.8-27B-FP8 262144 \
                   --pipeline-parallel-size 3 --max-num-seqs 16 --speculative-config '{"method":"mtp","num_speculative_tokens":3}' ;;
    # The 4-bit 27B (production's weights): one copy across both 3090s (twice the KV pool of the 8-bit: 14 GB of weights
    # instead of 29) against two copies, one per card, as production runs (owner, 10-18: "2 models on each card ... or 1
    # model on 2 cards but with a lot of agents"). -tp2b/-x2b: the same in batch mode, for the swarm.
    # CG=64: 16 seats x 4 tokens (3 drafts) per step stay inside the captured graphs; single mode's default of 32
    # covers 8 seats, and above that steps run uncaptured (slower), which would undercount 12 and 16 agents
    w4-27b-tp2)  XARGS="--tensor-parallel-size 2" hq_run "$arm" "$port" "$G3090,$G3090TI" single a MAX_LEN=262144 MAX_SEQS=16 \
                   CG=64 ;;
    # -tp2 peaked at 8 agents (342 tok/s) and fell at 12-16 (244-252, the slowest requests at 2-10 tok/s) with the KV
    # pool three-quarters full and nothing preempted, as the 8-bit 27B with MTP did (10-18): -tp2n drops the drafter,
    # -tp2m1 keeps it with one draft instead of three (2 tokens per seat per step instead of 4). Same weights, and the
    # drafts are accepted by probabilistic sampling, so the answers are the same model's: speed tests only
    w4-27b-tp2n) XARGS="--tensor-parallel-size 2" hq_run "$arm" "$port" "$G3090,$G3090TI" single a MAX_LEN=262144 MAX_SEQS=16 \
                   SPEC=off ;;
    w4-27b-tp2m1) XARGS="--tensor-parallel-size 2" hq_run "$arm" "$port" "$G3090,$G3090TI" single a MAX_LEN=262144 MAX_SEQS=16 \
                   DRAFT_TOKENS=1 ;;
    # -tp2r<n>/-tp2m1r<n> (10-21): at 128k conversations -tp2 and -tp2m1 stalled at 6 agents (first tokens 94-111 s, the
    # pool 70% full) while -tp2n held 8. With MTP on this hybrid model vLLM keeps a Mamba state checkpoint at every
    # block for the prefix cache ("defaulting prefix_cache_retention_interval to dense checkpointing"; its sparse
    # default never hits under MTP), each the size of a KV page, so cached conversations take far more of the pool and
    # are evicted sooner, and an evicted 100k conversation is read again at about 1k tokens/s while every other agent
    # waits. <n>: one checkpoint per n tokens, the cost a turn's re-read of up to n tokens. vLLM refuses an n that is
    # not a multiple of the block, which it sizes so a KV page holds a Mamba state: 832 tokens with three drafts, 816
    # with one. Result: -tp2m1r4080 held 8 agents at 128k (280 tok/s, 98% of prompt tokens from the cache) where
    # -tp2m1 stalled at 6 twice
    w4-27b-tp2r*) XARGS="--tensor-parallel-size 2 --prefix-cache-retention-interval ${arm#w4-27b-tp2r}" hq_run "$arm" "$port" \
                   "$G3090,$G3090TI" single a MAX_LEN=262144 MAX_SEQS=16 CG=64 ;;
    w4-27b-tp2m1r*) XARGS="--tensor-parallel-size 2 --prefix-cache-retention-interval ${arm#w4-27b-tp2m1r}" hq_run "$arm" \
                   "$port" "$G3090,$G3090TI" single a MAX_LEN=262144 MAX_SEQS=16 DRAFT_TOKENS=1 ;;
    w4-27b-x2)   hq_run "$arm" "$port" "$G3090TI" single a && hq_run "$arm-b" "$((port + 1))" "$G3090" single b
                 second=$((port + 1)) ;;
    w4-27b-tp2b) XARGS="--tensor-parallel-size 2" hq_run "$arm" "$port" "$G3090,$G3090TI" batch a MAX_LEN=65536 MAX_SEQS=128 \
                   GPU_UTIL=0.90 ;;   # the launcher's 0.93 under tensor parallel: the q9b-bf16 warm-up fault above
    w4-27b-x2b)  hq_run "$arm" "$port" "$G3090TI" batch a MAX_LEN=65536 MAX_SEQS=64 && \
                 hq_run "$arm-b" "$((port + 1))" "$G3090" batch b MAX_LEN=65536 MAX_SEQS=64
                 second=$((port + 1)) ;;
    # "parallel" is a key of Strata's saved config, read by its server (serve/server.py parallel_args), not a start
    # flag: a copy of IQ3_S's config with "parallel": 2, started through CONFIG (the first try passed --parallel 2 as
    # an argument, which the entry point drops, 10-18)
    fn-iq3s-2-p2) strata_cfg strata-iq3_s strata-iq3_s-p2 'c["parallel"] = 2' || return 1
                  CONFIG=/data/config/strata-iq3_s-p2.json strata_run "$arm" "$port" "$G3090,$G3090TI" qwen IQ3_S "$M/flash/IQ3_S/IQ3_S" 262144 ;;
    # The same with MTP kept: on a layer split, batch slots go through the cards in pipelined groups, and that path runs
    # no MTP drafts (Strata docs/BATCHING.md), which is why -p2 decoded slower than one request; serial windows keep it
    fn-iq3s-2-bm) strata_cfg strata-iq3_s strata-iq3_s-bm 'c["parallel"] = 2; c["args"] += ["--batch-mtp"]
c.setdefault("env", {})["STRATA_BATCH_MTP_SERIAL"] = "1"' || return 1
                  CONFIG=/data/config/strata-iq3_s-bm.json strata_run "$arm" "$port" "$G3090,$G3090TI" qwen IQ3_S "$M/flash/IQ3_S/IQ3_S" 262144 ;;
    # Strata's own setup for many conversations on a split (docs/BATCHING.md, MULTI_GPU.md): 4 slots in pipelined groups
    # (batch-groups auto: 2 groups of 2 on 2 stages; their measured 8 clients on 2 GPUs: 109 tok/s against 78 in one
    # group), each card loading only its own layers, which leaves room for the slots' state; 64k per conversation, the
    # agents' 40k with room, since every slot's state comes out of the expert cache (-p2 lost 3.1 GiB of it for one
    # more slot at 262k, 10-18). -bm measured only two slots with MTP; this is the setup made for 3 to 8 agents
    fn-iq3s-2-b4) strata_cfg strata-iq3_s strata-iq3_s-b4 'c["parallel"] = 4; c["args"] += ["--layer-split", "27", "--trim-stage-weights"]
i = c["args"].index("--max-context"); c["args"][i + 1] = "65536"' || return 1
                  CONFIG=/data/config/strata-iq3_s-b4.json strata_run "$arm" "$port" "$G3090,$G3090TI" qwen IQ3_S "$M/flash/IQ3_S/IQ3_S" 65536 ;;
    # -2s: the split -2t and -2h build on (layer 27, each card loading only its own layers), so each change shows alone
    fn-iq3s-2s)   strata_cfg strata-iq3_s strata-iq3_s-s 'c["args"] += ["--layer-split", "27", "--trim-stage-weights"]' || return 1
                  CONFIG=/data/config/strata-iq3_s-s.json strata_run "$arm" "$port" "$G3090,$G3090TI" qwen IQ3_S "$M/flash/IQ3_S/IQ3_S" 262144 ;;
    fn-iq3s-2h)   strata_cfg strata-iq3_s strata-iq3_s-h 'c["args"] += ["--layer-split", "27", "--trim-stage-weights", "--expert-cache-device1", "auto", "--remote-expert-opt"]; c["gpu"] = [0, 2, 1]' || return 1
                  GPUS_ORDER=0,2,1 CONFIG=/data/config/strata-iq3_s-h.json strata_run "$arm" "$port" "$G3090,$G3090TI,$G5060" qwen IQ3_S "$M/flash/IQ3_S/IQ3_S" 262144 ;;
    # Tuned as measured on another 2x3090 x16 + x4 rig (Strata docs/MULTI_GPU.md, #2418 and #1447): a split at layer 27
    # instead of auto (which priced the x4 link into K=31: 114-119 against 130-136 tok/s), each card loading only its own
    # layers' dense weights, and the async tier + pipelined windows + lag 2 + rotate + fused prompt (142 tok/s, prefill +28%)
    fn-iq3s-2t)   strata_cfg strata-iq3_s strata-iq3_s-t 'c["args"] += ["--layer-split", "27", "--trim-stage-weights", "--adapt-async", "1", "--pipeline-windows", "2"]
c.setdefault("env", {}).update(STRATA_ADAPT_LAG="2", STRATA_EXCHANGE_ROTATE="1", STRATA_PF_FUSED="1")' || return 1
                  CONFIG=/data/config/strata-iq3_s-t.json strata_run "$arm" "$port" "$G3090,$G3090TI" qwen IQ3_S "$M/flash/IQ3_S/IQ3_S" 262144 ;;
    # The owner's idea (10-18): the 5060 Ti as a store of experts, not a stage. A helper cache holds experts no stage
    # holds and computes them itself, so only activations cross its x1 link (Strata docs/SECOND_GPU.md); on the 3-card
    # split it was a stage with its own copy of the dense weights. -2t is the same split without the helper.
    # In the container nvidia-smi numbers the cards 3090 = 0, 5060 Ti = 1, 3090 Ti = 2: "gpu" [0, 2, 1] puts the stages on
    # the 3090s (CUDA0, CUDA1) and leaves CUDA2, the 5060 Ti, to the helper (helpers take the cards no stage runs on)
    fn-q4kxl-2t)  strata_cfg strata-unsloth-ud-q4_k_xl strata-q4kxl-t 'c["args"] += ["--layer-split", "27", "--trim-stage-weights"]; c["gpu"] = [0, 1]' || return 1
                  CONFIG=/data/config/strata-q4kxl-t.json strata_run "$arm" "$port" "$G3090,$G3090TI" unsloth UD-Q4_K_XL "$M/flash/UD-Q4_K_XL/UD-Q4_K_XL" 262144 ;;
    fn-q4kxl-2h)  strata_cfg strata-unsloth-ud-q4_k_xl strata-q4kxl-h 'c["args"] += ["--layer-split", "27", "--trim-stage-weights", "--expert-cache-device1", "auto", "--remote-expert-opt"]; c["gpu"] = [0, 2, 1]' || return 1
                  GPUS_ORDER=0,2,1 CONFIG=/data/config/strata-q4kxl-h.json strata_run "$arm" "$port" "$G3090,$G3090TI,$G5060" unsloth UD-Q4_K_XL "$M/flash/UD-Q4_K_XL/UD-Q4_K_XL" 262144 ;;
    # Off the NVMe drive (10-20, the owner: "the goal is to try to not use any of the nvme at all"). On -2h the three cards
    # hold 48 GiB of Q4's experts and 23.5 GiB stay outside VRAM, but setup gave the RAM copy 8 GiB (its rule for a 32 GB
    # PC: RAM minus 20-24 GB, for a desktop with a browser open; the engine started with 27.5 GiB available) and left the
    # rest to the OS file cache, which every long prompt flushes: the coding run read ~95 GB from the drive per answer.
    # Each arm adds one change: -2hr pins up to 23 GiB (the memory guard keeps 4 GiB, docs/UNSLOTH_Q4.md) and keeps the
    # VRAM reserve Strata's own warning asks for (421 MiB free, "decode falls to a few tok/s"); -2hrp reads prompts in
    # 2,048-token chunks, which borrow ~600 expert slots instead of ~2,400 whose experts then streamed from the SSD ("a
    # smaller --prefill keeps them all in RAM"); -2hrpk serves 131k instead of 262k (twice the owner's 64k floor), the
    # KV's VRAM going to expert slots; -2hrpks saves what its adaptive tier learns from coding prompts (expert-profile.py,
    # --expert-profile-save, #477) and -2hrpkc starts from that order instead of the shipped one, ranked for a document
    # summary (a code prompt finds 60% of its experts in a cache filled from it, Strata's tools/make_profile_mix.py);
    # -2hrpkcd adds STRATA_DISJOINT_ADAPT=1 (docs/DISJOINT_EXPERT_CACHE.md: the primary cache stops promoting experts the
    # helper holds, ~2,000 duplicate slots, +15% on their 2 x 3080 rig).
    fn-q4kxl-2hr*) local py='c["args"] += ["--layer-split", "27", "--trim-stage-weights", "--expert-cache-device1", "auto", "--remote-expert-opt"]; c["gpu"] = [0, 2, 1]
def put(flag, val):
    if flag in c["args"]: c["args"][c["args"].index(flag) + 1] = val
    else: c["args"] += [flag, val]
put("--resident-budget-gib", "23"); put("--vram-reserve-mib", "832")' ctx=262144
                  case $arm in fn-q4kxl-2hrp*) py="$py"'
put("--prefill", "2048")' ;; esac
                  case $arm in fn-q4kxl-2hrpk*) py="$py"'
put("--max-context", "131072")'; ctx=131072 ;; esac
                  case $arm in fn-q4kxl-2hrpks) py="$py"'
put("--expert-profile-save", "/data/expert-profile-code.bin"); put("--expert-profile-save-every", "5")' ;; esac
                  case $arm in fn-q4kxl-2hrpkc*) py="$py"'
put("--expert-profile", "/data/expert-profile-code.bin")' ;; esac
                  case $arm in fn-q4kxl-2hrpkcd) py="$py"'
c.setdefault("env", {}).update(STRATA_DISJOINT_ADAPT="1")' ;; esac
                  strata_cfg strata-unsloth-ud-q4_k_xl "strata-${arm#fn-}" "$py" || return 1
                  GPUS_ORDER=0,2,1 CONFIG="/data/config/strata-${arm#fn-}.json" strata_run "$arm" "$port" "$G3090,$G3090TI,$G5060" unsloth UD-Q4_K_XL "$M/flash/UD-Q4_K_XL/UD-Q4_K_XL" $ctx ;;
    # What -2hr's log showed (10-20): the guard cut the 23 GiB to 20.96 (25.2 GiB available at that point, minus its 4 GiB
    # headroom), 2.8 GiB of the 23.7 outside VRAM still came off the drive, and the answers read 603 GB against -2h's 969
    # while decoding 53 tok/s against 29. The headroom is not spare: once serving, the engine grows by ~5 GiB (pinned
    # staging, shared memory, its start-up heap pushed to swap) and the box ran at 0.2 GiB available, so the room has to
    # come from VRAM. And -2hrp never read 2,048-token chunks: Strata's setup.upgrade_config rewrites exactly "2048" to
    # "auto" when the server starts (configs from before v0.1.13), so -2hrp is -2hr again and -2hrpk is -2hr at 131k.
    # fn-q4kxl-2z-<letters>: -2hr plus one letter per change, so any combination can run. k: 131k context; q: 64k (the
    # owner's floor), more of the KV's VRAM to experts; d: STRATA_DISJOINT_ADAPT=1 (the 3090s' adaptive tiers stop
    # promoting experts the 5060 Ti holds: those are in neither the RAM copy nor the stage cache, so each such promotion
    # is a file read and a duplicate slot); p: 4,096-token prompt chunks (a fixed size the rewrite leaves alone), half the
    # slots lent to the prompt path; s: saves the coding order its tier learns; c: starts from that order.
    # Not --no-prefill-borrow: under a split each stage then withholds ~1.9 GiB of VRAM for the session (generate.cpp).
    fn-q4kxl-2z-*) local fl=${arm#fn-q4kxl-2z-} py='c["args"] += ["--layer-split", "27", "--trim-stage-weights", "--expert-cache-device1", "auto", "--remote-expert-opt"]; c["gpu"] = [0, 2, 1]
def put(flag, val):
    if flag in c["args"]: c["args"][c["args"].index(flag) + 1] = val
    else: c["args"] += [flag, val]
put("--resident-budget-gib", "23"); put("--vram-reserve-mib", "832")' ctx=262144
                  case $fl in *k*) py="$py"'
put("--max-context", "131072")'; ctx=131072 ;; esac
                  case $fl in *q*) py="$py"'
put("--max-context", "65536")'; ctx=65536 ;; esac
                  case $fl in *d*) py="$py"'
c.setdefault("env", {}).update(STRATA_DISJOINT_ADAPT="1")' ;; esac
                  case $fl in *p*) py="$py"'
put("--prefill", "4096")' ;; esac
                  case $fl in *s*) py="$py"'
put("--expert-profile-save", "/data/expert-profile-code.bin"); put("--expert-profile-save-every", "5")' ;; esac
                  case $fl in *c*) py="$py"'
put("--expert-profile", "/data/expert-profile-code.bin")' ;; esac
                  strata_cfg strata-unsloth-ud-q4_k_xl "strata-${arm#fn-}" "$py" || return 1
                  GPUS_ORDER=0,2,1 CONFIG="/data/config/strata-${arm#fn-}.json" strata_run "$arm" "$port" "$G3090,$G3090TI,$G5060" unsloth UD-Q4_K_XL "$M/flash/UD-Q4_K_XL/UD-Q4_K_XL" $ctx ;;
    # fn-iq3s-2z-<letters><n> (10-21): a coding order for IQ3_S, the quant the coding tests chose (Q4 tied it on the hard
    # problems, 44 of 72 tries each, at half the speed). The owner's question, 10-20: the coding experts on the 3090s,
    # more on the 5060 Ti, then RAM, then the drive; Strata ranks its tiers by a shipped profile built from document
    # summaries (its docs: a code prompt hit 60% of a cache filled from it, a summary 85%). -2s plus h: the 5060 Ti as
    # helper (-2h); s: saves the coding order its tier learns; c: starts from that order. A trailing number only names a
    # repeat: realwork keeps an arm's answers and would skip a second run under the same name.
    fn-iq3s-2z-*) local fl=${arm#fn-iq3s-2z-} gpus="$G3090,$G3090TI" order= py='c["args"] += ["--layer-split", "27", "--trim-stage-weights"]
def put(flag, val):
    if flag in c["args"]: c["args"][c["args"].index(flag) + 1] = val
    else: c["args"] += [flag, val]'
                  case $fl in *h*) py="$py"'
c["args"] += ["--expert-cache-device1", "auto", "--remote-expert-opt"]; c["gpu"] = [0, 2, 1]'; gpus="$gpus,$G5060"; order=0,2,1 ;; esac
                  case $fl in *s*) py="$py"'
put("--expert-profile-save", "/data/expert-profile-code-iq3s.bin"); put("--expert-profile-save-every", "5")' ;; esac
                  case $fl in *c*) py="$py"'
put("--expert-profile", "/data/expert-profile-code-iq3s.bin")' ;; esac
                  strata_cfg strata-iq3_s "strata-${arm#fn-}" "$py" || return 1
                  GPUS_ORDER=$order CONFIG="/data/config/strata-${arm#fn-}.json" strata_run "$arm" "$port" "$gpus" qwen IQ3_S "$M/flash/IQ3_S/IQ3_S" 262144 ;;
    # config 1: the largest model across all three cards
    fn-q4kxl-3)  strata_run "$arm" "$port" "$G3090,$G3090TI,$G5060" unsloth UD-Q4_K_XL "$M/flash/UD-Q4_K_XL/UD-Q4_K_XL" 262144 ;;
    fn-iq3s-3)   strata_run "$arm" "$port" "$G3090,$G3090TI,$G5060" qwen IQ3_S "$M/flash/IQ3_S/IQ3_S" 262144 ;;
    # swarm: small models, many requests at once (port 8082: the harness VM's agents reach it); text only: scouts read
    # code, and the vision encoder's memory goes to the KV pool instead. The tensor-parallel ones at 0.90 and 128 seats:
    # at 0.92 and 256, q9b-bf16 ran out of memory in vLLM's sampler warm-up (256 x a 250k vocabulary, 244 MiB, after the
    # pool was taken; 85 MiB left beside the two-card NCCL buffers, 10-18). The load test stops at 128 agents.
    a3b-fp8)     GPU_UTIL=0.90 vllm_run "$arm" "$port" "$G3090,$G3090TI" swarm/Qwen3.6-35B-A3B-FP8 65536 --tensor-parallel-size 2 --max-num-seqs 128  --language-model-only ;;
    q9b-bf16)    GPU_UTIL=0.90 vllm_run "$arm" "$port" "$G3090,$G3090TI" swarm/Qwen3.5-9B 65536 --tensor-parallel-size 2 --max-num-seqs 128  --language-model-only ;;
    q9b-bf16-dp) vllm_run "$arm" "$port" "$G3090,$G3090TI" swarm/Qwen3.5-9B 65536 --data-parallel-size 2 --max-num-seqs 128  --language-model-only ;;
    # 4-bit 35B-A3B (QuantTrio AWQ): the 8-bit one leaves ~1.5 GB of pool at 100 agents (2026-10-17 estimate)
    a3b-awq)     GPU_UTIL=0.90 vllm_run "$arm" "$port" "$G3090,$G3090TI" swarm/Qwen3.6-35B-A3B-AWQ 65536 --tensor-parallel-size 2 --max-num-seqs 128  --language-model-only ;;
    # a third copy of the 4B on the 5060 Ti beside the two on the 3090s (the swarm left the card idle)
    q4b-5060)    vllm_run "$arm" "$port" "$G5060" swarm/Qwen3.5-4B 65536 --max-num-seqs 128 --language-model-only ;;
    # the 9B on the 5060 Ti: 18 GB in bf16 does not fit 16, so Red Hat's fp8 checkpoint of it (Blackwell runs fp8
    # natively; 10-18, after the 4B's swarm and the 9B's on the 3090s). Not --quantization fp8 (10-19): vLLM loads the
    # whole bf16 model before quantizing it. KV in fp8 too: the ~3.5 GB left after ~10 GB of weights holds ~110k
    # tokens in bf16 (8 attention layers x 4 heads x 256 x 2 bytes x K and V = 32 KB a token), too few for 16 scouts
    q9b-5060)    vllm_run "$arm" "$port" "$G5060" swarm/Qwen3.5-9B-FP8-dynamic 65536 --kv-cache-dtype fp8 --max-num-seqs 64 \
                   --language-model-only ;;
    # main and swarm at once (10-18): the 4-bit 27B on the 3090s as main, the 4B on the 5060 Ti on the next port,
    # to see whether the two slow each other (the host's CPU, its PCIe root, the scouts' own threads)
    # combo-main-q4b (10-22, was combo-tp2-q4b before it ran): main as decided, -tp2m1r4080; -tp2's dense checkpoints
    # stall at the 8 agents x 128k the combo puts on it, which would measure the stall instead of running both at once
    combo-main-q4b) XARGS="--tensor-parallel-size 2 --prefix-cache-retention-interval 4080" hq_run "$arm" "$port" \
                     "$G3090,$G3090TI" single a MAX_LEN=262144 MAX_SEQS=16 DRAFT_TOKENS=1 && \
                   vllm_run "$arm-b" "$((port + 1))" "$G5060" swarm/Qwen3.5-4B 65536 --max-num-seqs 128 --language-model-only
                   second=$((port + 1)) ;;
    q4b-bf16-dp) vllm_run "$arm" "$port" "$G3090,$G3090TI" swarm/Qwen3.5-4B 65536 --data-parallel-size 2 --max-num-seqs 128  --language-model-only ;;
    *) echo "unknown arm $arm (arms: $ARMS)" >&2; return 1 ;;
  esac
  local url="http://127.0.0.1:$port" up="" first=""
  for _ in $(seq 1 360); do   # up to 60 min: Strata's first start prepares the model's pack
    if curl -sf -m 5 "$url/health" >/dev/null 2>&1; then up=$(( $(date +%s) - t0 )); break; fi
    if ! sudo docker ps -q --filter "name=^bench-$arm$" | grep -q .; then   # the whole log: the cause is above the traceback
      mkdir -p results; sudo docker logs "bench-$arm" > "results/$arm.start.log" 2>&1
      echo "bench-$arm exited (results/$arm.start.log):"
      grep -iE "error|exception|out of memory|not supported|unsupported" "results/$arm.start.log" | grep -v "^\s*File " | tail -15
      return 1
    fi
    sleep 10
  done
  [ -n "$up" ] || { echo "bench-$arm not up after 60 min"; sudo docker logs "bench-$arm" > "results/$arm.start.log" 2>&1; return 1; }
  if [ -n "$second" ]; then   # the other server of a two-server arm
    for _ in $(seq 1 120); do curl -sf -m 5 "http://127.0.0.1:$second/health" >/dev/null 2>&1 && break; sleep 5; done
    curl -sf -m 5 "http://127.0.0.1:$second/health" >/dev/null 2>&1 || { echo "bench-$arm-b not up"; sudo docker logs "bench-$arm-b" > "results/$arm-b.start.log" 2>&1; return 1; }
    sudo docker logs "bench-$arm-b" > "results/$arm-b.start.log" 2>&1
    up=$(( $(date +%s) - t0 ))
  fi
  curl -sf -m 600 "$url/v1/chat/completions" -H 'Content-Type: application/json' \
    -d '{"model":"bench","messages":[{"role":"user","content":"Say hi."}],"max_tokens":8,"chat_template_kwargs":{"enable_thinking":false},"reasoning_effort":"none"}' >/dev/null \
    && first=$(( $(date +%s) - t0 ))
  mkdir -p results; sudo docker logs "bench-$arm" > "results/$arm.start.log" 2>&1   # KV cache size, the engine's choices
  printf '{"arm":"%s","health_s":%s,"first_token_s":%s,"at":"%s"}\n' "$arm" "$up" "${first:-null}" "$(date -Is)" \
    | tee -a "results/$arm.switch.json"
}

stop_all() {
  local c n; c=$(sudo docker ps -aq --filter "name=^bench-")
  for n in $(sudo docker ps --format '{{.Names}}' --filter "name=^bench-fn-"); do   # Strata's own log: split, caches per card
    sudo docker exec "$n" sh -c 'cat "$(ls -t /opt/strata/strata-*.log | head -1)"' > "results/${n#bench-}.engine.log" 2>&1 || true
  done
  # vLLM's own log (throughput, draft acceptance, KV use every 10 s): the 4-bit 27B with MTP fell at 12-16 agents and
  # its log went with the container (10-18)
  for n in $(sudo docker ps --format '{{.Names}}' --filter "name=^bench-" | grep -v '^bench-fn-'); do
    sudo docker logs "$n" > "results/${n#bench-}.engine.log" 2>&1 || true
  done
  if [ -n "$c" ]; then sudo docker stop -t 60 $c >/dev/null; sudo docker rm $c >/dev/null; fi
}

case ${1:-} in
  start) start "${2:?arm}" "${3:-8093}" ;;
  stop)  stop_all ;;
  list)  echo "$ARMS" ;;
  *) sed -n 2,14p "$0"; exit 1 ;;
esac
