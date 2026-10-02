#!/bin/bash
# frontpage live preview (agent account): follows `main` of the team project and serves it on 127.0.0.1:8420.
# Within a minute of every merge: export main, reuse or build a pinned .venv (--require-hashes), restart the app.
# One data dir for every build (database + model files). A fetch runs right after a restart only when the newest
# item is over an hour old, so frequent merges do not hammer the sources. Never runs in the main checkout (the
# agents fast-forward it; files written there would block their merges).
# Stop: kill the pid in ~/frontpage-preview/run.pid (it stops the app too).
set -u
R=$HOME/frontpage-preview; M=$HOME/projects/frontpage; PORT=8420
mkdir -p "$R/data" "$R/builds"; echo $$ > "$R/run.pid"
cur=""; srv=""
trap '[ -n "$srv" ] && kill "$srv" 2>/dev/null; exit 0' TERM INT
log() { echo "$(date '+%F %T') $*" >> "$R/preview.log"; }
while true; do
  head=$(git -C "$M" rev-parse --short main 2>/dev/null)
  if [ -n "$head" ] && [ "$head" != "$cur" ]; then
    B=$R/builds/$head; rm -rf "$B"; mkdir -p "$B"; git -C "$M" archive main | tar -x -C "$B"
    V=$R/venv-$(sha256sum < "$B/requirements.txt" | cut -c1-16)
    if [ ! -x "$V/bin/python" ]; then
      if python3 -m venv --system-site-packages "$V" && "$V/bin/pip" install -q --require-hashes -r "$B/requirements.txt" >> "$R/preview.log" 2>&1; then
        log "venv built: $V"
      else log "venv FAILED for $head - keeping the running build"; rm -rf "$V"; cur=$head; sleep 60; continue; fi
    fi
    sed -e "s#^listen: .*#listen: 127.0.0.1:$PORT#" -e "s#^data_dir: .*#data_dir: $R/data#" "$B/config.sample.yaml" > "$B/config.yaml"
    (cd "$B" && "$V/bin/python" -c "from frontpage import model, config; print(model.ensure_model(config.load_config('config.yaml')))") >> "$R/preview.log" 2>&1 \
      || log "model files not ready (the app falls back and says so)"
    if [ -n "$srv" ]; then kill "$srv" 2>/dev/null; wait "$srv" 2>/dev/null; fi
    (cd "$B" && exec "$V/bin/python" -m frontpage --config config.yaml >> "$R/server.log" 2>&1) & srv=$!
    cur=$head; log "serving $head ($(git -C "$M" log -1 --format=%s main | cut -c1-90))"
    (cd "$B" && "$V/bin/python" "$R/cycle.py" >> "$R/preview.log" 2>&1) &
    ls -1dt "$R"/builds/* | tail -n +4 | xargs -r rm -rf   # keep the newest 3 builds
  fi
  sleep 60
done
