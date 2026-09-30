#!/usr/bin/env bash
# auto.sh : unattended sequencer for effort A/B v2 (VM, as claude, started with setsid nohup).
# 1. wait for GPU_FREE (the session that holds the GPU touches it when it hands over) and a healthy model server;
# 2. classifier self-test (the auto-responder must route known requests right);
# 3. two real smoke probes (xhigh and medium) and a plumbing sanity check on them;
# 4. the probe phase, then the project phase (run.py go; resumable).
# Any failed check stops it with a reason in auto.log and a STOPPED file. A HOLD file stops it after the smoke runs.
cd /home/claude/ab-effort2-kit || exit 1
log() { echo "$(date -u '+%F %T') $*" >> auto.log; }
log "auto.sh started (pid $$); waiting for GPU_FREE"
until [ -f GPU_FREE ]; do sleep 60; done
until curl -sf -m 5 127.0.0.1:8080/v1/models > /dev/null; do sleep 30; done
log "model server up; classifier self-test"
python3 run.py cltest >> auto.log 2>&1 || { log "STOP: classifier self-test failed"; touch STOPPED; exit 1; }
for s in "p_contra xhigh" "p_missing medium"; do
  log "smoke $s"
  python3 run.py smoke $s >> auto.log 2>&1
done
python3 - >> auto.log 2>&1 <<'PY' || { log "STOP: smoke sanity check failed"; touch STOPPED; exit 1; }
import json, sys
bad = 0
for rid in ("smoke-p_contra-xhigh", "smoke-p_missing-medium"):
    r = f"results/{rid}"
    try:
        ledger = [json.loads(l) for l in open(f"{r}/iterations.jsonl") if l.strip()]
        grade = json.load(open(f"{r}/grade.json"))
        loop = open(f"{r}/loop.log").read()
    except Exception as e:
        print(rid, "sanity FAILED: missing output", repr(e)); bad += 1; continue
    errs = [x.get("session_error") for x in ledger if x.get("session_error")]
    ok = len(ledger) >= 1 and not errs and ("core" in grade or "import" in grade)
    print(rid, "sanity", "ok" if ok else "FAILED", f"sessions={len(ledger)} verified={'verified done' in loop} errors={errs[:2]} grade={json.dumps(grade)[:200]}")
    bad += not ok
sys.exit(bad)
PY
[ -f HOLD ] && { log "HOLD present: stopping after the smoke runs"; exit 0; }
log "smoke ok; probe phase"
python3 run.py go probes >> run.log 2>&1 || { log "STOP: probe phase exited with an error (see run.log)"; touch STOPPED; exit 1; }
log "probe phase done; project phase"
python3 run.py go project >> run.log 2>&1 || { log "STOP: project phase exited with an error (see run.log)"; touch STOPPED; exit 1; }
log "ALL DONE"
touch ALL_DONE
