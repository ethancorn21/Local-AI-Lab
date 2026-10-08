#!/usr/bin/env bash
# archive/tests/test_archive.sh: the lab archive end to end, on a Linux host with GNU tar, zstd, git and python3 >= 3.9
# (the AI box). Fake projects -> lab-archive-send -> lab-archive-ingest -> lab-archive sample/seal/export -> checks.
# Nothing outside a temp dir is touched.
set -uo pipefail
D=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'kill $HTTP 2>/dev/null; rm -rf "$T"' EXIT
fails=0; ok() { echo "ok   $1"; }; bad() { echo "FAIL $1"; fails=$((fails + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
P=$T/projects R=$T/root
mkdir -p "$P" "$R" "$T/kit"
cat > "$T/ingest" <<EOF
#!/bin/sh
LAB_ARCHIVE_ROOT=$R LAB_ARCHIVE_MIN_FREE=\${LAB_ARCHIVE_MIN_FREE:-0} exec python3 $D/lab-archive-ingest "\$@"
EOF
chmod +x "$T/ingest"
send() { LAB_PROJECTS=$P LAB_ARCHIVE_LIST=$T/kit/list LAB_ARCHIVE_HIDDEN=$T/kit/hidden LAB_ARCHIVE_STATE=$T/state \
         LAB_ARCHIVE_TARGET=$T/ingest bash "$D/lab-archive-send" 2>&1; }
uploads() { find "$R/vm" -name '*.tar.zst' 2>/dev/null | sort; }
newest() { uploads | tail -1; }
members() { zstd -dcq "$1" | tar -t; }
g() { git -C "$1" -c user.email=t@t -c user.name=t "${@:2}"; }

# ---- fake projects: a solo project (both session formats), a team (main + agent a), a hidden one, an unlisted one
mkdir -p "$P/demo/.agent/sessions/iter-0002.d" "$P/tm/.agent/team" "$P/tm.a/.agent/sessions" "$P/secret/.agent" "$P/other/.agent"
cat > "$P/demo/.agent/iterations.jsonl" <<'EOF'
{"iter":1,"task":"tasks/001-hello.md","start":"2026-10-01T10:00:00-05:00","end":"2026-10-01T10:05:00-05:00","agent_secs":290,"agent_commits":2,"verify":"accepted","status_after":"done","harness":{"big":"x"}}
{"iter":2,"task":"tasks/002-more.md","start":"2026-10-01T10:10:00-05:00","end":"2026-10-01T10:20:00-05:00","agent_secs":500,"agent_commits":1,"verify":null}
EOF
cat > "$P/demo/.agent/sessions/iter-0001.jsonl" <<'EOF'
{"type":"session","version":3,"id":"s1","timestamp":"2026-10-01T15:00:01.000Z","cwd":"/x"}
{"type":"message_end","message":{"role":"assistant","model":"qwen-test","timestamp":1790866801000,"stopReason":"toolUse","usage":{"input":1000,"output":200,"cacheRead":0,"reasoning":150},"content":[{"type":"toolCall","name":"bash","id":"1","arguments":{}}]}}
{"type":"turn_end","message":{"role":"assistant","model":"qwen-test","timestamp":1790866801000,"usage":{"input":1000,"output":200,"reasoning":150},"content":[{"type":"toolCall","name":"bash"}]}}
{"type":"message_end","message":{"role":"toolResult","isError":true,"timestamp":1790866802000,"toolName":"bash"}}
not json at all
{"type":"message_end","message":{"role":"assistant","model":"qwen-test","timestamp":1790866803000,"stopReason":"length","usage":{"input":3000,"output":500,"reasoning":100},"content":[]}}
EOF
printf '%s\n' '{"type":"session","timestamp":"2026-10-01T15:10:00Z"}' \
  '{"type":"message","message":{"role":"assistant","model":"qwen-test","timestamp":1790867400000,"usage":{"input":500,"output":50},"content":[]}}' \
  > "$P/demo/.agent/sessions/iter-0002.d/2026-10-01T15-10-00-000Z_s2.jsonl"
ln -s iter-0002.d/2026-10-01T15-10-00-000Z_s2.jsonl "$P/demo/.agent/sessions/iter-0002.jsonl"
echo x > "$P/demo/.agent/team.lock"
g "$P/demo" init -q && echo hi > "$P/demo/README" && g "$P/demo" add README && g "$P/demo" commit -qm one
cat > "$P/tm/.agent/team/events.jsonl" <<'EOF'
{"time":"2026-10-01T12:00:00-05:00","agent":"a","event":"claim","task":"tasks/101-x.md","detail":""}
{"time":"2026-10-01T12:03:00-05:00","agent":"a","event":"merge","task":"tasks/101-x.md","detail":"abc"}
{"time":"2026-10-01T12:03:05-05:00","agent":"a","event":"wait_start","task":"","detail":"102 waits for 101; "}
{"time":"2026-10-01T12:33:05-05:00","agent":"a","event":"wait_end","task":"tasks/102-y.md","detail":"1800"}
EOF
echo '{"iter":1,"task":"tasks/101-x.md","start":"2026-10-01T12:00:00-05:00","end":"2026-10-01T12:02:00-05:00","agent_secs":110,"verify":"accepted"}' > "$P/tm.a/.agent/iterations.jsonl"
echo '{"type":"message_end","message":{"role":"assistant","timestamp":1790874000000,"usage":{"input":10,"output":5},"content":[]}}' > "$P/tm.a/.agent/sessions/iter-0001.jsonl"
g "$P/tm" init -q && echo t > "$P/tm/f" && g "$P/tm" add f && g "$P/tm" commit -qm t
echo leak > "$P/secret/.agent/iterations.jsonl"; echo leak > "$P/other/.agent/iterations.jsonl"
printf 'demo\n# a comment\ntm  \nsecret\n../escape\n' > "$T/kit/list"; echo secret > "$T/kit/hidden"
sleep 1

# ---- send
out=$(send); echo "$out" | sed 's/^/     /'
check "first send stored one upload" '[ "$(uploads | wc -l)" = 1 ]'
m=$(members "$(newest)")
check "upload has the solo ledger, both sessions and the symlink" 'grep -qx "demo/.agent/iterations.jsonl" <<<"$m" && grep -qx "demo/.agent/sessions/iter-0001.jsonl" <<<"$m" && grep -qx "demo/.agent/sessions/iter-0002.jsonl" <<<"$m" && grep -q "iter-0002.d/2026" <<<"$m"'
check "upload has the team's events and agent a's checkout" 'grep -qx "tm/.agent/team/events.jsonl" <<<"$m" && grep -qx "tm.a/.agent/iterations.jsonl" <<<"$m"'
check "upload has a bundle per repository and a manifest" 'grep -qx "bundles/demo.bundle" <<<"$m" && grep -qx "bundles/tm.bundle" <<<"$m" && grep -qx MANIFEST <<<"$m"'
check "hidden, unlisted and bad names are never shipped" '! grep -qE "^(secret|other)/|escape" <<<"$m"'
check "lock files are left out" '! grep -q "team.lock" <<<"$m"'
check "the bundle restores the project" 'd=$T/x; mkdir -p $d; zstd -dcq "$(newest)" | tar -x -C $d bundles/demo.bundle && git clone -q $d/bundles/demo.bundle $d/clone && [ -f $d/clone/README ]'
check "uploads are read-only" '[ -z "$(find "$R/vm" -name "*.tar.zst" -perm /222)" ]'
out=$(send)
check "nothing changed: nothing sent" 'grep -q "nothing new" <<<"$out" && [ "$(uploads | wc -l)" = 1 ]'
echo '{"iter":3,"task":"tasks/002-more.md","start":"2026-10-01T10:30:00-05:00","end":"2026-10-01T10:31:00-05:00","agent_secs":50}' >> "$P/demo/.agent/iterations.jsonl"
sleep 1; send > /dev/null
m=$(members "$(newest)")
check "a changed ledger: only it goes, without bundles" '[ "$(uploads | wc -l)" = 2 ] && grep -qx "demo/.agent/iterations.jsonl" <<<"$m" && ! grep -q "sessions\|\.bundle$" <<<"$m"'
echo more >> "$P/demo/README"; g "$P/demo" commit -qam two; sleep 1; send > /dev/null
check "a new commit: a new bundle of that project only" 'm=$(members "$(newest)"); grep -qx "bundles/demo.bundle" <<<"$m" && ! grep -qx "bundles/tm.bundle" <<<"$m"'
check "a failed upload is resent" 'n=$(uploads | wc -l); echo x >> "$P/tm/.agent/team/events.jsonl"; LAB_PROJECTS=$P LAB_ARCHIVE_LIST=$T/kit/list LAB_ARCHIVE_HIDDEN=$T/kit/hidden LAB_ARCHIVE_STATE=$T/state LAB_ARCHIVE_TARGET=/bin/false bash "$D/lab-archive-send" 2>/dev/null; [ $? = 1 ] && [ "$(uploads | wc -l)" = $n ] && send >/dev/null && members "$(newest)" | grep -qx "tm/.agent/team/events.jsonl"'

# ---- ingest refusals: nothing stored, nothing left behind
n=$(uploads | wc -l)
z=$T/z.zst; echo payload | zstd -q > "$z"; zs=$(stat -c %s "$z"); zh=$(sha256sum "$z" | cut -c1-64)
check "no size and checksum: refused" '[ "$("$T/ingest" < "$z")" = "refused usage" ]'
check "a cut-off upload: refused" '[ "$(head -c 5 "$z" | "$T/ingest" $zs $zh)" = "refused incomplete" ]'
check "not zstd: refused" 'h=$(echo hi | sha256sum | cut -c1-64); [ "$(echo hi | "$T/ingest" 3 $h)" = "refused not-zstd" ]'
check "over the cap: refused" '[ "$(LAB_ARCHIVE_CAP=4 "$T/ingest" $zs $zh < "$z")" = "refused too-big" ]'
check "no space: refused" '[ "$(LAB_ARCHIVE_MIN_FREE=$((1 << 62)) "$T/ingest" $zs $zh < "$z")" = "refused disk-full" ]'
check "refusals stored nothing and left no parts" '[ "$(uploads | wc -l)" = $n ] && [ -z "$(find "$R/vm" -name ".*.part")" ]'
# the VM can send anything that passes ingest: a zstd that is no tar, a ledger with a non-object line and a bad row
check "a zstd that is no tar is stored (ingest does not parse)" '[ "$("$T/ingest" $zs $zh < "$z" | cut -d" " -f1)" = stored ]'
printf '%s\n' '[1, 2]' '{"iter": "x", "start": 5, "agent_secs": "x", "task": "tasks/009-bad.md"}' >> "$P/demo/.agent/iterations.jsonl"
sleep 1; send > /dev/null

# ---- AI box side: hardware log with the UTC -> local switch, model server counters, then seal + export
{ echo "time,cpu_pkg_c,cpu_max_core_c,gpu,name,gpu_c,gpu_w,gpu_fan_pct,gpu_util_pct,thermal_slowdown"
  for m in 00 01 02 03 04 05; do for s in 00 15 30 45; do echo "2026-10-01T15:$m:$s,60,61,0,RTX 3090 Ti,70,300,80,95,0"; done; done
  for m in 00 01 02; do for s in 00 15 30 45; do echo "2026-10-01T12:$m:$s,60,61,0,RTX 3090 Ti,65,200,70,90,0"; done; done   # local time now
  echo "garbage,row"; } > "$T/hwtemps.csv"
mkdir -p "$T/www"; PORT=$((20000 + RANDOM % 20000))
printf '# HELP x\nvllm:generation_tokens_total{model="q"} 1000\nvllm:num_requests_running{model="q"} 1\nvllm:kv_cache_usage_perc{model="q"} 0.25\n' > "$T/www/metrics"
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$T/www" >/dev/null 2>&1 & HTTP=$!
cat > "$T/conf.json" <<EOF
{"root": "$R", "hwtemps": "$T/hwtemps.csv", "timezone": "America/Chicago",
 "servers": {"$PORT": {"gpu": "RTX 3090 Ti", "kind": "vllm"}, "1": {"gpu": "none", "kind": "vllm"}},
 "agent_gpu": {"a": "RTX 3090 Ti", "solo": "RTX 3090 Ti"}}
EOF
LA() { LAB_ARCHIVE_CONFIG=$T/conf.json python3 "$D/lab-archive" "$@"; }
sleep 1; LA sample; sed -i 's/ 1000$/ 1600/' "$T/www/metrics"; LA sample
check "sample: one line per server per run, a down server recorded as down" 'f=$(ls $R/hw/servers/*.jsonl); [ "$(wc -l < $f)" = 4 ] && grep -q "\"up\": false" $f'
LA nightly 2> "$T/nightly.log"; sed 's/^/     /' "$T/nightly.log"
E=$R/export
col() { python3 -c 'import csv,sys; r=[x for x in csv.DictReader(open(sys.argv[1])) if all(x[k]==v for k,v in (a.split("=",1) for a in sys.argv[3:]))]; print("|".join(x[sys.argv[2]] for x in r))' "$@"; }
check "seal: the day's hardware file, times made UTC across the clock switch" 'f=$R/hw/hwtemps/2026-10-01.csv; [ -f $f ] && grep -q "^2026-10-01T15:00:00Z" $f && grep -q "^2026-10-01T17:00:00Z" $f && [ ! -w $f ] && grep -q "clock went back" "$T/nightly.log"'
check "sessions: all 4 ledger rows, newest ledger copy wins" '[ "$(col $E/sessions.csv iteration project=demo)" = "1|2|3" ] && [ "$(col $E/sessions.csv agent project=tm)" = a ]'
check "sessions: tokens from a headless session (turn_end not counted twice)" '[ "$(col $E/sessions.csv output_tokens project=demo iteration=1)" = 700 ] && [ "$(col $E/sessions.csv reasoning_tokens project=demo iteration=1)" = 250 ] && [ "$(col $E/sessions.csv peak_context_tokens project=demo iteration=1)" = 3500 ] && [ "$(col $E/sessions.csv tool_errors project=demo iteration=1)" = 1 ] && [ "$(col $E/sessions.csv responses_cut_at_length project=demo iteration=1)" = 1 ]'
check "sessions: a TUI session through its iter symlink" '[ "$(col $E/sessions.csv output_tokens project=demo iteration=2)" = 50 ]'
check "sessions: energy from the UTC rows (6 min x 300 W) and the local rows (3 min x 200 W)" '[ "$(col $E/sessions.csv gpu_energy_wh project=demo iteration=1)" = 30.0 ] && [ "$(col $E/sessions.csv gpu_energy_wh project=tm)" = 10.0 ]'
check "sessions: no reading, no energy (not zero)" '[ -z "$(col $E/sessions.csv gpu_energy_wh project=demo iteration=2)" ]'
check "tasks: merged team task with its done time; solo task accepted" '[ "$(col $E/tasks.csv done_utc project=tm task_id=101)" = 2026-10-01T17:03:00Z ] && [ "$(col $E/tasks.csv done_utc project=demo task_id=001)" = 2026-10-01T15:05:00Z ] && [ -z "$(col $E/tasks.csv done_utc project=demo task_id=002)" ]'
check "waits: the 30-minute wait on dependencies" '[ "$(col $E/waits.csv minutes project=tm agent=a)" = 30.0 ] && [ "$(col $E/waits.csv waiting_on project=tm)" = dependencies_claims_size ]'
check "team events: all of them" '[ "$(col $E/team_events.csv event project=tm | tr "|" "\n" | grep -c .)" = 4 ]'
check "servers: tokens per minute from the counter" '[ "$(col $E/servers_minute.csv gen_tokens port=$PORT)" = "|600" ]'
check "hardware: minutes from both sides of the switch" 'grep -q "^2026-10-01T15:00:00Z" $E/hardware_minute.csv && grep -q "^2026-10-01T17:02:00Z" $E/hardware_minute.csv'
check "nothing hidden reached the export" '! grep -rq "secret\|leak" $E'
check "junk from the VM costs only itself: the no-tar upload and the bad ledger row are logged, the rest exported" 'grep -q "is not a readable tar" "$T/nightly.log" && grep -q "skipped a ledger row of demo" "$T/nightly.log" && ! grep -q "009" $E/sessions.csv'
cp $E/sessions.csv "$T/s1"; LA export 2> "$T/again.log"
check "a second export reads the cache, same result" '! grep -q "^lab-archive: read " "$T/again.log" && cmp -s $E/sessions.csv "$T/s1"'

echo; [ $fails = 0 ] && echo "ALL OK" || { echo "$fails FAILED"; exit 1; }
