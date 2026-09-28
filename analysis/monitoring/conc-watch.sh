#!/bin/bash
# A/B watch: session starts per arm, exceptions, arm/test end. One stdout line = one event.
t0=$(date +%s); seen=-1   # -1: the first poll only counts what is already there (re-arming does not replay it)
while [ $(( $(date +%s) - t0 )) -lt 1740 ]; do
  out=$(ssh -n -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 harnessvm 'sudo cat /home/agent/ab/concurrency2.log 2>/dev/null; for a in C1 C2; do sudo sed "s/^/[$a] /" /home/agent/ab/$a/hollowdeep/.agent/loop.log 2>/dev/null; done; sudo test -f /home/agent/ab/CONC_DONE && echo "CONC_DONE"; true' 2>"${TMPDIR:-/tmp}/ab-watch-ssh.err"); rc=$?
  [ $rc -ne 0 ] && { echo "$(date +%T) poll failed rc=$rc: $(tail -1 "${TMPDIR:-/tmp}/ab-watch-ssh.err")" >&2; sleep 60; continue; }
  lines=$(grep -E '^== |^\[(C1|C2)\] .*(iteration [0-9]+:|verified done|REJECTED|STALLED|STUCK|queue empty|unreachable|timeout|hand-over guard|malformed|WARNING: acceptance|WARNING: session)|CONC_DONE' <<<"$out" | sed -E 's/^(\[(C1|C2)\]) [0-9-]+ /\1 /')
  # report lines not reported before (a set, not a tail: both arms' logs are merged, so new lines appear mid-list)
  SEEN="${TMPDIR:-/tmp}/conc-watch.seen"
  if [ "$seen" -lt 0 ]; then printf '%s\n' "$lines" | sort -u > "$SEEN"; seen=0
  else
    new=$(comm -13 "$SEEN" <(printf '%s\n' "$lines" | sort -u))
    [ -n "$new" ] && { printf '%s\n' "$new" | sort -k2 | cut -c1-150; printf '%s\n' "$lines" | sort -u > "$SEEN"; }
  fi
  echo "$(date +%T) poll ok" >&2
  grep -q CONC_DONE <<<"$out" && exit 0
  sleep 60
done
