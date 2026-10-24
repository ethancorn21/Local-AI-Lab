#!/usr/bin/env python3
"""bench-load: speed of one OpenAI-compatible server, alone and under many agents at once (vLLM or Strata).

single: one request at a time, production settings (thinking on, effort xhigh, temperature 1.0 / top_p 0.95 /
        top_k 20): decode tok/s on a short prompt and on ~50k and ~150k token prompts, time to first token for a cold
        prompt (a random first line defeats the prefix cache) and for the same prompt again (cached), and the context
        the server says it serves.
load:   N simulated agents at once for a fixed window, for each N in --levels. Each agent works like a coding agent:
        a prompt made of a part every agent shares (the project, --shared tokens, default 8k) and its own part
        (--own, default 7k), up to --max-tokens of thinking and answer, then its next turn with its own answer
        appended, until its conversation passes --cap tokens (default 40k: a scout's limit) and it starts over. Per level,
        all inside the window: total tok/s generated; decode tok/s while a request streams (median and slowest tenth);
        each agent's share (its tokens / the window: what it gets once queueing and prefill are counted); time to
        first token (median and 95th percentile; a request still waiting at the end counts its wait so far) and how
        many were still waiting; errors; and from vLLM's /metrics the most of the KV cache used and the requests it
        preempted. Open requests are cut when the window closes and the server drains before the next level. --stop-below ends the sweep after the
        first level whose median agent falls under that many tok/s.
Why (2026-10-17): the two-3090 bench compares serving setups by how many agents they carry at a usable speed, and
the old engine-bench.py measured one request only.

usage: bench-load.py single --url http://127.0.0.1:8093/v1 --label NAME [--model NAME] [--source SET.json]
       bench-load.py load --url ... --label NAME --levels 1,2,4,8,16,32,64 [--window 300] [--stop-below 20]
       bench-load.py report
"""
import argparse, http.client, json, os, random, socket, statistics, threading, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results")
CHARS_PER_TOKEN = 3.6
SHORT = ("Write a JavaScript ES module implementing a binary min-heap priority queue with push, pop, peek, size and "
         "decreaseKey(id, newPriority) (O(log n) via an id->index map), with JSDoc, then node:test unit tests "
         "covering every method.")
TASK = ("\n\n---\nYou are one of several engineers on this codebase. Pick the last file above, find its weakest "
        "function, explain the problem, and write an improved version with tests.")


def source_text(path):
    """Prompt material: the long-context set's largest context (real code), so prompts look like agent prompts."""
    s = json.load(open(path))
    return max((d["context"] for d in s["depths"].values()), key=len)


def chars(tokens):
    return int(tokens * CHARS_PER_TOKEN)


def stream(url, model, prompt, max_tokens, on_tokens=None, seed=None, think=True, on_open=None):
    """One streamed request. on_tokens(n_new) is called as tokens arrive. Returns timings and token counts.
    on_open(conn) gets the connection before the request goes out, so another thread can cut it with abort(conn):
    the request then returns what it had, marked aborted, and the server cancels it (vLLM and Strata both do)."""
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 1.0, "top_p": 0.95, "top_k": 20, "stream": True,
            "stream_options": {"include_usage": True, "continuous_usage_stats": True},
            "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True, "reasoning_effort": "xhigh"}
            if think else {"enable_thinking": False}}
    if seed is not None:
        body["seed"] = seed
    u = urllib.parse.urlsplit(url + "/chat/completions")
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=3600)
    t0 = time.monotonic(); first = last = None; n = 0; usage = {}; text = []
    try:
        conn.connect()
        # kept for abort(): http.client lets go of conn.sock when the reply says "Connection: close", as every Strata
        # reply does, and abort() then cut nothing (10-18: Strata ran each cut request to its end, into the next level)
        conn.raw_sock = conn.sock
        if on_open:
            on_open(conn)
        conn.request("POST", u.path, body=json.dumps(body), headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}: {r.read(300)!r}")
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            ev = json.loads(line[5:])
            usage = ev.get("usage") or usage
            got = False
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                piece = d.get("content") or d.get("reasoning_content") or d.get("reasoning")
                if piece:
                    got = True; text.append(d.get("content") or "")
            if got:
                now = time.monotonic(); first = first or now; last = now
                # servers with continuous usage report exact counts (speculative decoding sends several per chunk)
                new = (usage.get("completion_tokens") or 0) - n if usage.get("completion_tokens") else 1
                n += new
                if on_tokens and new > 0:
                    on_tokens(new)
    except (OSError, ValueError, http.client.HTTPException):
        if not getattr(conn, "aborted", False):
            raise
    finally:
        conn.close()
    n = usage.get("completion_tokens") or n
    return {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": n,
            "ttft_s": round(first - t0, 2) if first else None, "wall_s": round(time.monotonic() - t0, 2),
            "decode_tps": round((n - 1) / (last - first), 1) if first and last and last > first and n > 1 else None,
            "aborted": getattr(conn, "aborted", False), "text": "".join(text)}


def abort(conn):
    """Cut a stream() from another thread: shutting the socket wakes a read blocked on it."""
    conn.aborted = True
    sock = getattr(conn, "raw_sock", None) or conn.sock
    try:
        if sock:
            sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def served_context(url):
    base = url.rsplit("/v1", 1)[0]
    for path, key in (("/v1/models", None), ("/health", "max_context")):
        try:
            d = json.load(urllib.request.urlopen(base + path, timeout=10))
        except Exception:
            continue
        if key and d.get(key):
            return d[key]
        for m in d.get("data") or []:
            if m.get("max_model_len"):
                return m["max_model_len"]
    return None


def count_tokens(url, model, prompt):
    """The server's own token count of a prompt as stream() sends it (vLLM's /tokenize); None where there is none
    (Strata)."""
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode()
    try:
        req = urllib.request.Request(url.rsplit("/v1", 1)[0] + "/tokenize", data=body,
                                     headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=60))["count"]
    except Exception:
        return None


def metrics(url):
    """vLLM's /metrics as {name: summed value}; {} for servers without it (Strata)."""
    try:
        txt = urllib.request.urlopen(url.rsplit("/v1", 1)[0] + "/metrics", timeout=5).read().decode()
    except Exception:
        return {}
    out = {}
    for line in txt.splitlines():
        if line.startswith("vllm:") and " " in line:
            name = line.split("{")[0].split(" ")[0]
            try:
                out[name] = out.get(name, 0.0) + float(line.rsplit(" ", 1)[1])
            except ValueError:
                pass
    return out


def single(a, text):
    a = argparse.Namespace(**{**vars(a), "url": a.url.split(",")[0]})   # a two-server arm: one server's own speed
    ctx = served_context(a.url)
    rows = [{"label": a.label, "test": "served_context", "tokens": ctx}]
    prompts = [("short", SHORT)]
    for k in a.sizes:
        if ctx is None or k + 4000 < ctx:
            prompts.append((f"{k // 1000}k", text[:chars(k)] + TASK))
    for kind, p in prompts:
        for rep in range(a.reps):
            cold = f"[run {random.getrandbits(48):x}]\n" + p
            for test, prompt in (("cold", cold), ("cached", cold)):   # the second send hits the prefix cache
                if kind == "short" and test == "cached":
                    continue
                try:
                    r = stream(a.url, a.model, prompt, a.max_tokens, seed=rep + 1)
                    r.pop("text")
                except Exception as e:
                    r = {"error": str(e)[:200]}
                row = {"label": a.label, "test": f"{kind}_{test}", "rep": rep, **r}
                rows.append(row)
                print(json.dumps(row), flush=True)
    return rows


def merged_metrics(urls):
    """metrics() of every server, summed (a two-server arm: --url a,b)."""
    out = {}
    for u in urls:
        for k, v in metrics(u).items():
            out[k] = out.get(k, 0.0) + v
    return out


def drain(urls, model, limit=300):
    """Wait until no server holds a request, so a level never starts under the last one's leftovers; returns the
    seconds it took (a long drain means cut requests ran on). vLLM says so in /metrics. Strata has none and serves one
    request at a time, so a one-token request, answered only after everything queued ahead of it, is the all-clear."""
    t0 = time.monotonic()
    for u in urls:
        if not metrics(u):
            try:
                stream(u, model, "Say ok.", 1, think=False)
            except Exception:
                pass
    while time.monotonic() < t0 + limit:
        ms = [m for m in map(metrics, urls) if m]
        if all(m.get("vllm:num_requests_running", 0) + m.get("vllm:num_requests_waiting", 0) == 0 for m in ms):
            break
        time.sleep(2)
    return round(time.monotonic() - t0)


def load_level(a, text, n):
    # --url a,b (2026-10-18): two servers, e.g. one model per card; agent i talks to server i mod 2, as a router would.
    # Everything is measured inside the window, partial requests included, and the window's open requests are cut at
    # its end. Why (2026-10-18): the first version counted only requests that both started and finished inside it, so
    # at 8+ agents (a 3,000-token answer at ~20 tok/s outlasts the window) the per-agent speed came back empty; and the
    # requests still open at the end ran on into the next level's warmup and window, slowing it.
    # --pregrow: each agent starts partway through its conversation, at a random length between a fresh prompt and the
    # cap, as agents that started at different times would be; the window opens once every agent is streaming (their
    # first, cold prompts are filled) or after --warmup-max seconds, whichever comes first: past the KV cache's room
    # some agents never start, and that shows as their share and the waits. Why (2026-10-18): with every agent starting fresh, a 3-minute window sees only
    # the first turn or two of a conversation that takes ~20 minutes to reach the cap, so the KV cache sits far below
    # its working load and 12-16 agents look better than they can sustain.
    # Fit to the server (10-19): --cap is turned into characters at CHARS_PER_TOKEN, and the pieces a pregrown
    # conversation is made of tokenize denser than that (3.1 characters a token against 3.6), so the swarm's first 64k
    # sweep sent 66k-token prompts to 65,536-token servers: every request came back 400 and was sent again unchanged,
    # and the sweep recorded 203 errors and nothing else, twice. Where the server counts tokens (vLLM), each agent's
    # longest prompt is held to what fits beside its answer, and a pregrown one is cut to that; an agent whose request
    # fails starts its conversation over instead of resending it. Servers with room to spare (the main arms) see the
    # same prompts as before.
    urls = a.url.split(",")
    limits = [served_context(u) for u in urls]
    trimmed = [0]
    shared = text[:chars(a.shared)]
    win = [float("inf"), float("inf")]   # the window, set when it opens
    lock = threading.Lock(); tokens = [0]; share = [0] * n; rates = []; ttft = []; done = [0]; waiting = [0]
    errors = [0]; stop = threading.Event(); conns = set(); streaming = set(); all_streaming = threading.Event()
    kv_max = [0.0]

    def opened(conn):
        with lock:
            conns.add(conn)
            if stop.is_set():
                abort(conn)

    def agent(i):
        rng = random.Random(i)
        own_at = chars(a.shared) + rng.randrange(0, max(1, len(text) - chars(a.shared + a.own)))
        fresh = shared + text[own_at:own_at + chars(a.own)] + TASK
        convo = fresh
        if a.pregrow:   # earlier turns: answer-sized pieces of the same code, as the agent's own answers would be
            room = max(0, a.cap - a.shared - a.own - a.max_tokens - 500)
            grown_by = rng.randrange(0, room + 1) if room else 0
            while grown_by > 0:
                piece = min(grown_by, a.max_tokens); at = rng.randrange(0, max(1, len(text) - chars(piece)))
                convo += "\n\n" + text[at:at + chars(piece)] + "\n\nContinue: improve it further."
                grown_by -= piece
        url, most = urls[i % len(urls)], chars(a.cap) - chars(a.max_tokens)   # the longest prompt it sends
        if limits[i % len(urls)]:
            room, grown_to = limits[i % len(urls)] - a.max_tokens - 256, convo
            for _ in range(3):
                n_tok = count_tokens(url, a.model, convo)
                if not n_tok:
                    break
                most = min(most, int(room * len(convo) / n_tok))
                if n_tok <= room:
                    break
                convo = convo[:most]
            with lock:
                trimmed[0] += len(convo) < len(grown_to)
        while not stop.is_set():
            t_start = time.monotonic()
            st = {"first": None, "last": None, "n": 0, "n0": 0}   # this request's tokens inside the window

            def count(new):
                now = time.monotonic()
                if i not in streaming:
                    with lock:
                        streaming.add(i)
                        if len(streaming) == n:
                            all_streaming.set()
                if win[0] <= now <= win[1]:
                    with lock:
                        tokens[0] += new; share[i] += new
                    if st["first"] is None:
                        st["first"], st["n0"] = now, new
                    st["last"] = now; st["n"] += new
            try:
                r = stream(url, a.model, convo, a.max_tokens, on_tokens=count, seed=rng.getrandbits(31),
                           think=a.think == "on", on_open=opened)
            except Exception:
                with lock:
                    errors[0] += 1
                convo = fresh
                time.sleep(2)
                continue
            with lock:
                # decode speed while streaming, over the part inside the window; 20 tokens at least, since a few
                # tokens (or one speculative burst) give no rate
                if st["n"] - st["n0"] >= 20 and st["last"] > st["first"]:
                    rates.append((st["n"] - st["n0"]) / (st["last"] - st["first"]))
                if win[0] <= t_start <= win[1]:   # first-token wait of the requests sent inside the window
                    if r["ttft_s"] is not None:
                        ttft.append(r["ttft_s"])
                    else:   # still waiting when the window closed: counted at the wait so far, a lower bound
                        ttft.append(round(win[1] - t_start, 2)); waiting[0] += 1
                    done[0] += not r["aborted"]
            if r["aborted"]:
                break
            grown = convo + "\n\n" + r["text"] + "\n\nContinue: improve it further."
            convo = grown if len(grown) < most else fresh

    def sampler():
        while not stop.is_set():
            kv_max[0] = max([kv_max[0]] + [metrics(u).get("vllm:kv_cache_usage_perc", 0.0) for u in urls])
            time.sleep(5)

    m0 = None; t_begin = time.monotonic()
    threads = [threading.Thread(target=agent, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    threading.Thread(target=sampler, daemon=True).start()
    time.sleep(a.warmup)
    if a.pregrow:
        all_streaming.wait(timeout=max(0.0, a.warmup_max - a.warmup))
    m0 = merged_metrics(urls)
    with lock:
        win[0] = time.monotonic(); win[1] = win[0] + a.window
    time.sleep(a.window); m1 = merged_metrics(urls)
    with lock:
        stop.set()
        for c in conns:
            abort(c)
    for t in threads:
        t.join(timeout=60)
    drained = drain(urls, a.model)
    rates.sort(); ttft.sort(); per_agent = sorted(s / a.window for s in share)
    # share of the window's prompt tokens served from the prefix cache (10-21: at 128k the MTP arms stalled at 6 agents
    # with the pool 70% full; an agent whose cached turn is gone reads its whole conversation again)
    delta = lambda k: m1.get(k, 0) - m0.get(k, 0) if m1 and m0 else 0
    q = delta("vllm:prefix_cache_queries_total")
    prefix_hit = round(delta("vllm:prefix_cache_hits_total") / q, 3) if q > 0 else None
    pick = lambda xs, q: round(xs[min(len(xs) - 1, int(q * len(xs)))], 2) if xs else None
    return {"label": a.label, "test": "load", "agents": n, "cap": a.cap, "think": a.think, "window_s": a.window,
            "pregrow": a.pregrow, "trimmed": trimmed[0], "warmup_s": round(win[0] - t_begin),
            "aggregate_tps": round(tokens[0] / a.window, 1), "requests": len(ttft), "finished": done[0],
            "waiting_at_end": waiting[0], "errors": errors[0],
            "agent_tps_median": round(statistics.median(rates), 1) if rates else None, "agent_tps_p10": pick(rates, 0.1),
            "agent_share_median": round(statistics.median(per_agent), 1), "agent_share_min": round(per_agent[0], 1),
            "ttft_median_s": round(statistics.median(ttft), 2) if ttft else None, "ttft_p95_s": pick(ttft, 0.95),
            "kv_max": round(kv_max[0], 3) if m1 else None,
            "preemptions": (m1.get("vllm:num_preemptions_total", 0) - m0.get("vllm:num_preemptions_total", 0))
            if m1 and m0 else None, "drain_s": drained, "prefix_hit": prefix_hit}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["single", "load", "report"])
    ap.add_argument("--url"); ap.add_argument("--label"); ap.add_argument("--model", default="x")
    ap.add_argument("--source", default=os.path.join(HERE, "longctx-frontpage.json"))
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--sizes", type=lambda v: [int(x) for x in v.split(",")], default=[50_000, 150_000],
                    help="single: the long prompts, in tokens (10-20: realwork times one cold 50k prompt on arms of 64k-262k)")
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--levels", default="1,3,6"); ap.add_argument("--window", type=int, default=300)
    ap.add_argument("--warmup", type=int, default=60); ap.add_argument("--stop-below", type=float)
    ap.add_argument("--cap", type=int, default=40000, help="tokens: an agent's conversation starts over past this")
    ap.add_argument("--shared", type=int, default=8000); ap.add_argument("--own", type=int, default=7000)
    ap.add_argument("--think", choices=["on", "off"], default="on", help="off: scout-shaped turns, no thinking")
    ap.add_argument("--pregrow", action="store_true", help="agents start partway through their conversations")
    ap.add_argument("--warmup-max", type=int, default=300, help="--pregrow: open the window by then at the latest")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.cmd == "report":
        for fn in sorted(f for f in os.listdir(OUT) if f.endswith(".load.jsonl")):
            for r in map(json.loads, open(f"{OUT}/{fn}")):
                if r["test"] == "load":
                    print(f"{r['label']:28s} {r['agents']:4d} agents @{r.get('cap', 40000) // 1000}k  total {r['aggregate_tps']:7.1f} tok/s  each "
                          f"{r['agent_tps_median']} (slowest {r['agent_tps_p10']})  share {r.get('agent_share_median')} "
                          f"(least {r.get('agent_share_min')})  ttft {r['ttft_median_s']}s p95 {r['ttft_p95_s']}s "
                          f"waiting {r.get('waiting_at_end')}  kv {r['kv_max']}  preempted {r['preemptions']}  errors {r['errors']}  drain {r.get('drain_s')}s")
                elif r["test"] != "served_context" and "error" not in r:
                    print(f"{r['label']:28s} {r['test']:14s} {r.get('prompt_tokens')} tok  ttft {r.get('ttft_s')}s  "
                          f"decode {r.get('decode_tps')} tok/s")
                elif r["test"] == "served_context":
                    print(f"{r['label']:28s} serves {r['tokens']} tokens")
        return
    text = source_text(a.source)
    path = f"{OUT}/{a.label}.load.jsonl"
    if a.cmd == "single":
        rows = single(a, text)
        with open(path, "a") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        return
    for n in (int(x) for x in a.levels.split(",")):
        row = load_level(a, text, n)
        print(json.dumps(row), flush=True)
        open(path, "a").write(json.dumps(row) + "\n")
        if a.stop_below and (row["agent_tps_median"] or 0) < a.stop_below:
            print(f"stopping: median agent {row['agent_tps_median']} tok/s < {a.stop_below}", flush=True)
            break


if __name__ == "__main__":
    main()
