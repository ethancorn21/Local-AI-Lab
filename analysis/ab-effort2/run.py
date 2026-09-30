"""Effort A/B v2 orchestrator (runs on the harness VM as `claude`; the arms run as `agent`).

Measures reasoning effort (THINKING=xhigh vs medium) on the lab's real workflow, not only "can it code":
  probes   6 one-task projects: 4 with one planted spec flaw each (contradiction, missing information, ambiguity,
           wrong reference) and 2 clean controls (false-alarm rate). Seeded with tasks/001, no GOAL.md.
  project  a multi-step security tool (authwatch) from a GOAL.md through the whole loop: planning, tasks, goal check.
           GOAL.md holds two planted flaws.
Each run: wait until the model server has been idle for a minute (nothing else on the GPU), set up a fresh project,
run agent-loop with production settings except THINKING, answer ask_human requests from the run's answer key
(auto-responder, the local model classifies which planted flaw a request is about; anything else gets a generic
"choose, record the assumption" reply), sample vLLM load every 20 s (contamination check), then grade the result with
the hidden check. Everything hidden (answer keys, checks, reference implementation) lives in KIT/hidden, readable only
by `claude`; the agent never sees it. Doorbell rings from the arms go to a fake ring-doorbell (no Telegram).

Usage (on the VM, as claude):  run.py plan   -> KIT/schedule.json
                                run.py go [PHASE]  (probes|project|all; resumable, skips finished runs)
                                run.py status
                                run.py smoke NAME EFFORT   (one extra run outside the schedule, id smoke-*)
                                run.py cltest   (does the auto-responder's classifier route known requests right?)
"""
import json
import os
import random
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

KIT = "/home/claude/ab-effort2-kit"
AB = "/home/agent/ab-effort2"
RUNS = f"{AB}/runs"
FAKEBIN = f"{AB}/fakebin"
LAUNCH = os.environ.get("AB_LAUNCH", f"{AB}/ab-launch")   # override only for plumbing tests
VLLM = "http://127.0.0.1:8080"
PROBES = {"p_contra": "fwtop", "p_missing": "anon", "p_ambig": "daily", "p_wrongref": "sshtools", "c_sums": "sumcheck",
          "c_failcount": "failcount"}
EFFORTS = ["xhigh", "medium"]
PROBE_REPS, PROJECT_REPS = 4, 3
LIMITS = {"probe": {"max_iters": 6, "goal_checks": 0, "hard_hours": 1.5},
          "project": {"max_iters": 80, "goal_checks": 3, "hard_hours": 12}}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"{now()} {msg}", flush=True)


def sh(cmd, agent=False, check=True, inp=None, timeout=None):
    argv = ["sudo", "-u", "agent", "-H", "bash", "-c", cmd] if agent else ["bash", "-c", cmd]
    p = subprocess.run(argv, input=inp, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"failed ({p.returncode}): {cmd[:200]}\n{p.stderr[-800:]}")
    return p.stdout


def put(path, text, mode="644"):
    """Write a file as agent."""
    sh(f"cat > {shlex.quote(path)} && chmod {mode} {shlex.quote(path)}", agent=True, inp=text)


# ---------- model server ----------
def metrics():
    try:
        body = urllib.request.urlopen(f"{VLLM}/metrics", timeout=10).read().decode()
    except OSError:
        return None
    val = lambda name: sum(float(m) for m in re.findall(rf"^vllm:{name}(?:{{[^}}]*}})? ([0-9.e+]+)$", body, re.M))
    return {"running": val("num_requests_running"), "waiting": val("num_requests_waiting"),
            "gen_tokens": val("generation_tokens_total"), "prompt_tokens": val("prompt_tokens_total")}


def wait_gpu_idle(quiet_s=60):
    if os.environ.get("AB_SKIP_IDLE"):   # plumbing tests only
        return
    idle_since, last_note = None, 0
    while True:
        m = metrics()
        busy = m is None or m["running"] > 0 or m["waiting"] > 0
        t = time.time()
        if busy:
            idle_since = None
            if t - last_note > 600:
                log(f"waiting for the model server to be up and idle ({'down' if m is None else m})")
                last_note = t
        else:
            idle_since = idle_since or t
            if t - idle_since >= quiet_s:
                return
        time.sleep(5)


def classify(request, flaws):
    """Which planted flaws does this request raise? The local model decides (thinking off, temperature 0)."""
    if not flaws:
        return []
    listing = "\n".join(f"- {f['id']}: {f['description']}" for f in flaws)
    prompt = (f"A developer working on a task sent this request to the task's author:\n\n<request>\n{request[:6000]}\n"
              f"</request>\n\nKnown issues in the task description:\n{listing}\n\nWhich of the known issues does the "
              "request raise or ask about (directly or in other words)? Reply with only JSON: {\"ids\": [...]}, an "
              "empty list if none.")
    body = {"model": "qwen3.8-27b", "messages": [{"role": "user", "content": prompt}], "temperature": 0,
            "max_tokens": 60, "chat_template_kwargs": {"enable_thinking": False}}
    try:
        req = urllib.request.Request(f"{VLLM}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        text = json.loads(urllib.request.urlopen(req, timeout=120).read())["choices"][0]["message"]["content"]
        ids = json.loads(re.search(r"\{.*\}", text, re.S).group(0)).get("ids", [])
        return [i for i in ids if any(f["id"] == i for f in flaws)]
    except Exception as e:  # classification failed: generic answer, logged
        return [f"error: {e}"[:120]]


# ---------- runs ----------
def plan():
    rng = random.Random(20260930)
    sched, n = [], 0
    for rep in range(1, PROBE_REPS + 1):
        names = list(PROBES)
        rng.shuffle(names)
        for k, name in enumerate(names):
            order = EFFORTS if (rep + k) % 2 else EFFORTS[::-1]
            for eff in order:
                n += 1
                sched.append({"id": f"r{n:03d}", "phase": "probes", "kind": "probe", "name": name, "effort": eff, "rep": rep})
    for rep, order in zip(range(1, PROJECT_REPS + 1), [EFFORTS, EFFORTS[::-1], EFFORTS]):
        for eff in order:
            n += 1
            sched.append({"id": f"r{n:03d}", "phase": "project", "kind": "project", "name": "authwatch", "effort": eff, "rep": rep})
    json.dump(sched, open(f"{KIT}/schedule.json", "w"), indent=1)
    print(f"{len(sched)} runs: {sum(r['kind'] == 'probe' for r in sched)} probe, {sum(r['kind'] == 'project' for r in sched)} project")


def ensure_fakes():
    sh(f"mkdir -p {RUNS} {FAKEBIN}", agent=True)
    put(f"{FAKEBIN}/ring-doorbell", "#!/bin/sh\n# effort A/B: no Telegram. Swallow the ask text, report success.\n"
        "cat > /dev/null 2>&1 &\necho \"rang (test run: no message sent)\"\nexit 0\n", "755")
    put(f"{AB}/ab-launch", f"#!/bin/bash -l\n# ab-launch EFFORT MAX_ITERS GOAL_CHECKS DIR : agent-loop with production settings except THINKING,\n"
        f"# asks answered by the A/B's auto-responder, doorbell faked.\nexport PATH={FAKEBIN}:$PATH RING_DOORBELL={FAKEBIN}/ring-doorbell\n"
        "export THINKING=\"$1\" MAX_ITERS=\"$2\" GOAL_CHECKS=\"$3\"\ncd \"$4\" && exec agent-loop \"$4\"\n", "755")


def setup(r):
    rdir = f"{RUNS}/{r['id']}"
    pdir = f"{rdir}/{PROBES.get(r['name'], r['name'])}"
    sh(f"rm -rf {rdir} && mkdir -p {pdir}", agent=True)
    if r["kind"] == "probe":
        tar = subprocess.run(["tar", "-C", f"{KIT}/hidden/probes/{r['name']}/seed", "-cf", "-", "."], capture_output=True, check=True).stdout
        subprocess.run(["sudo", "-u", "agent", "tar", "-C", pdir, "-xf", "-"], input=tar, check=True)
        sh(f"cd {pdir} && git init -q -b agent/work && git add -A && git commit -q -m 'seed: task 001' && git rev-parse HEAD", agent=True)
    else:
        put(f"{pdir}/GOAL.md", open(f"{KIT}/hidden/project/GOAL.md").read())
    return pdir


def answer_asks(r, pdir, res, answered):
    key = json.load(open(f"{KIT}/hidden/{'probes/' + r['name'] if r['kind'] == 'probe' else 'project'}/answers.json"))
    files = sh(f"ls {pdir}/.agent/asks/ 2>/dev/null | grep -E '^[0-9]+\\.md$' || true", agent=True).split()
    for f in files:
        path = f"{pdir}/.agent/asks/{f}"
        if path in answered:
            continue
        text = sh(f"cat {shlex.quote(path)}", agent=True, check=False)
        if not re.search(r"(?m)^status: open$", text):
            continue
        req = text.split("## Request", 1)[-1].strip()
        ids = classify(req, key["flaws"])
        if any(str(i).startswith("error") for i in ids):   # model server hiccup: leave it open, retry next poll
            log(f"{r['id']}: classifier failed for {f} ({ids[0]}) - retrying next poll")
            continue
        parts = [f["answer"] for f in key["flaws"] if f["id"] in ids]
        reply = "\n\n".join(parts) if parts else key["generic_answer"]
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        sh(f"printf '\\n## Answer (%s, %s)\\n%s\\n' {shlex.quote(stamp)} typed-reply {shlex.quote(reply)} >> {shlex.quote(path)} && "
           f"sed -i 's/^status: open$/status: answered/' {shlex.quote(path)}", agent=True)
        answered.add(path)
        with open(f"{res}/responder.jsonl", "a") as fh:
            fh.write(json.dumps({"time": now(), "ask": f, "request": req, "classified": ids, "answer": reply}) + "\n")
        log(f"{r['id']}: answered {f} (classified {ids or 'none'})")


def loop_pids(pdir):
    out = subprocess.run(["pgrep", "-u", "agent", "-f", f"agent-loop {pdir}"], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def stop_run(pdir):
    for pid in loop_pids(pdir):
        subprocess.run(["sudo", "kill", "-TERM", f"-{os.getpgid(pid)}"], capture_output=True)
    pi = sh(f"cat {pdir}/.agent/pi.pid 2>/dev/null || true", agent=True).strip()
    if pi.isdigit():
        subprocess.run(["sudo", "kill", "-TERM", f"-{pi}"], capture_output=True)
        subprocess.run(["sudo", "kill", "-TERM", pi], capture_output=True)


def grade(r, pdir, res):
    tmp = sh("mktemp -d /tmp/abgrade.XXXXXX", agent=True).strip()
    try:
        if r["kind"] == "probe":
            put(f"{tmp}/check.py", open(f"{KIT}/hidden/probes/{r['name']}/check.py").read())
            cmd = f"cd /tmp && timeout 300 python3 {tmp}/check.py {pdir}"
        else:
            for f in ("grade_project.py", "ref.py"):
                put(f"{tmp}/{f}", open(f"{KIT}/hidden/project/{f}").read())
            cmd = f"cd /tmp && timeout 1800 python3 {tmp}/grade_project.py {pdir}"
        out = sh(cmd, agent=True, check=False, timeout=2000).strip().splitlines()
        g = json.loads(out[-1]) if out else {"error": "no output"}
    except Exception as e:
        g = {"error": repr(e)[:300]}
    finally:
        sh(f"rm -rf {tmp}", agent=True, check=False)
    json.dump(g, open(f"{res}/grade.json", "w"), indent=1)
    return g


def execute(r):
    res = f"{KIT}/results/{r['id']}"
    os.makedirs(res, exist_ok=True)
    lim = LIMITS[r["kind"]]
    wait_gpu_idle()
    pdir = setup(r)
    log(f"{r['id']}: start {r['kind']} {r['name']} effort={r['effort']} rep={r.get('rep')} dir={pdir}")
    meta = {**r, "dir": pdir, "start": now(), "driver_sha": sh("sha256sum /home/agent/bin/agent-loop | cut -c1-12", agent=True).strip(),
            "extensions": sh("cd /home/agent/.pi/agent/extensions && sha256sum *.ts | cut -c1-12,65-", agent=True).split("\n")}
    json.dump(meta, open(f"{res}/meta.json", "w"), indent=1)
    t0 = time.time()
    with open(f"{res}/loop.out", "w") as out:
        proc = subprocess.Popen(["sudo", "-u", "agent", "-H", LAUNCH, r["effort"], str(lim["max_iters"]), str(lim["goal_checks"]), pdir],
                                stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    answered, capped = set(), False
    with open(f"{res}/vllm.csv", "w") as mc:
        mc.write("time,running,waiting,gen_tokens,prompt_tokens\n")
        while proc.poll() is None:
            m = metrics()
            if m:
                mc.write(f"{now()},{m['running']:.0f},{m['waiting']:.0f},{m['gen_tokens']:.0f},{m['prompt_tokens']:.0f}\n")
                mc.flush()
            try:
                answer_asks(r, pdir, res, answered)
            except Exception as e:
                log(f"{r['id']}: responder error {e!r}"[:300])
            if time.time() - t0 > lim["hard_hours"] * 3600 and not capped:
                log(f"{r['id']}: hard cap {lim['hard_hours']} h reached - stopping the loop")
                stop_run(pdir)
                capped = True
            time.sleep(20)
    meta.update(end=now(), wall_s=round(time.time() - t0), rc=proc.returncode, hard_capped=capped)
    for f in ("loop.log", "iterations.jsonl"):
        subprocess.run(["sudo", "cp", f"{pdir}/.agent/{f}", f"{res}/{f}"], capture_output=True)
    subprocess.run(["sudo", "chown", "-R", "claude:claude", res], capture_output=True)
    g = grade(r, pdir, res)
    meta["grade_summary"] = {k: g.get(k) for k in ("core", "core_score", "flaw", "flaws", "categories", "error") if k in g}
    json.dump(meta, open(f"{res}/meta.json", "w"), indent=1)
    open(f"{res}/status", "w").write("done\n")
    log(f"{r['id']}: done in {meta['wall_s'] / 60:.1f} min, grade {json.dumps(meta['grade_summary'])[:300]}")


def go(phase="all"):
    ensure_fakes()
    sched = json.load(open(f"{KIT}/schedule.json"))
    for r in sched:
        if phase != "all" and r["phase"] != phase:
            continue
        st = f"{KIT}/results/{r['id']}/status"
        if os.path.exists(st) and open(st).read().strip() == "done":
            continue
        if os.path.exists(f"{KIT}/HOLD"):   # someone needs the GPU: stop between runs; `run.py go` resumes later
            log(f"HOLD file present - stopping before {r['id']}")
            sys.exit(3)
        execute(r)
    log(f"phase {phase}: all runs finished")
    open(f"{KIT}/DONE-{phase}", "w").write(now() + "\n")


# Classifier self-test cases live in hidden/cltest.json: they describe the planted flaws.



def cltest():
    cases = json.load(open(f"{KIT}/hidden/cltest.json"))
    bad = 0
    for c in cases:
        task, req, want = c["task"], c["request"], c["want"]
        key = json.load(open(f"{KIT}/hidden/{'project' if task == 'project' else 'probes/' + task}/answers.json"))
        got = sorted(classify(req, key["flaws"]))
        ok = got == sorted(want)
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {task:10s} want {want} got {got}")
    print(f"classifier: {len(cases) - bad}/{len(cases)} correct")
    return bad


def status():
    sched = json.load(open(f"{KIT}/schedule.json"))
    for r in sched:
        res = f"{KIT}/results/{r['id']}"
        st = open(f"{res}/status").read().strip() if os.path.exists(f"{res}/status") else ("started" if os.path.exists(f"{res}/meta.json") else "-")
        g = json.load(open(f"{res}/meta.json")).get("grade_summary", {}) if st == "done" else {}
        w = json.load(open(f"{res}/meta.json")).get("wall_s") if st == "done" else None
        print(f"{r['id']} {r['phase']:7s} {r['name']:12s} {r['effort']:6s} rep{r['rep']} {st:8s} "
              f"{'' if w is None else f'{w / 60:6.1f} min'} {json.dumps(g)[:150]}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "plan":
        plan()
    elif cmd == "go":
        go(sys.argv[2] if len(sys.argv) > 2 else "all")
    elif cmd == "cltest":
        sys.exit(1 if cltest() else 0)
    elif cmd == "smoke":
        ensure_fakes()
        name, eff = sys.argv[2], sys.argv[3]
        execute({"id": f"smoke-{name}-{eff}", "phase": "smoke", "kind": "probe" if name in PROBES else "project",
                 "name": name, "effort": eff, "rep": 0})
    else:
        status()
