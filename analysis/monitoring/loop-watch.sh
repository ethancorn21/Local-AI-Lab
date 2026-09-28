#!/bin/bash
# 29-min watch: loop problems/task changes/malformed-call markers + AI box temps. One stdout line = one event.
L=/home/agent/projects/hollowdeep/.agent/loop.log; F=/var/log/hwtemps.csv
ln=$(ssh -n -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 harnessvm "sudo wc -l $L" 2>/dev/null | cut -d' ' -f1)
tn=$(ssh -n -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 aibox "wc -l < $F" 2>/dev/null)
mk=$(ssh -n -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 harnessvm "sudo sh -c 'cat /home/agent/projects/hollowdeep/.agent/sessions/*.stderr 2>/dev/null' | grep -c '\[loop\]'" 2>/dev/null)
t0=$(date +%s); maxc=0; maxg=0; minc=999; ming=999; lasttask=""; stopped=0; hot=0
while [ $(( $(date +%s) - t0 )) -lt ${DUR:-1500} ]; do
  sleep ${SLEEP:-60}
  out=$(ssh -n -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 harnessvm "c=\$(sudo wc -l $L | cut -d' ' -f1); echo N \$c; [ \$c -gt ${ln:-0} ] && sudo sed -n \"$(( ${ln:-0} + 1 )),\${c}p\" $L; pgrep -f 'bin/agent-l[o]op' >/dev/null && echo RUN 1 || echo RUN 0; echo MK \$(sudo sh -c 'cat /home/agent/projects/hollowdeep/.agent/sessions/*.stderr 2>/dev/null' | grep -c '\[loop\]'); sudo tail -1 /home/agent/projects/hollowdeep/.agent/iterations.jsonl | jq -r '\"WEB \\(.iter) \\(.web_searches // 0) \\(.web_fetches // 0)\"'" 2>/dev/null) || { echo "harness VM unreachable"; continue; }
  n=$(awk '/^N /{print $2}' <<<"$out"); [ -n "$n" ] && ln=$n
  grep -vE '^(N|RUN|MK|WEB) ' <<<"$out" | grep -E 'REJECTED|STALLED|STUCK|unreachable|server back|queue empty|timeout|blocked|TOP-LEVEL|PROPOSAL|WARNING: acceptance|WARNING: codemap|WARNING: decisions|malformed|flaky' | grep -v 'over Pi.s 50 KB'
  t=$(grep -oE 'iteration [0-9]+: tasks/[0-9]+' <<<"$out" | tail -1 | grep -oE '[0-9]+$')
  [ -n "$t" ] && [ -n "$lasttask" ] && [ "$t" != "$lasttask" ] && echo "loop moved to task $t"
  [ -n "$t" ] && lasttask=$t
  # down for two checks in a row (a deliberate pause-and-restart takes under a minute); re-armed once it runs again
  run=$(awk '/^RUN /{print $2}' <<<"$out")
  if [ "$run" = 0 ]; then down=$(( ${down:-0} + 1 )); [ "$down" -eq 2 ] && [ $stopped = 0 ] && { echo "LOOP NOT RUNNING (2+ min): $(grep -vE '^(N|RUN|MK|WEB) ' <<<"$out" | tail -1 | cut -c1-120)"; stopped=1; }
  else down=0; stopped=0; fi
  read -r _ wi ws wf < <(grep '^WEB ' <<<"$out"); if [ -n "$wi" ] && [ "$wi" != "${lastweb:-}" ]; then lastweb=$wi; [ "$((ws + wf))" -gt 0 ] && echo "iteration $wi used research: $ws searches, $wf fetches"; fi
  m=$(awk '/^MK /{print $2}' <<<"$out"); [ -n "$m" ] && [ "${m:-0}" -gt "${mk:-0}" ] && { echo "malformed-call recovery fired ($m markers total)"; mk=$m; }
  rows=$(ssh -n -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 aibox "c=\$(wc -l < $F); [ \$c -gt ${tn:-0} ] && sed -n \"$(( ${tn:-0} + 1 )),\${c}p\" $F; echo N \$c" 2>/dev/null) || { echo "AI box unreachable"; continue; }
  tn=$(awk '/^N /{print $2}' <<<"$rows")
  read c g < <(grep -v '^N ' <<<"$rows" | awk -F, 'NF>=5 && $3+0>0 {if($3>c)c=$3; if($4>g)g=$4} END{print c+0, g+0}')
  lo=$(grep -v '^N ' <<<"$rows" | awk -F, 'NF>=5 && $3+0>0 {if(!c||$3<c)c=$3; if(!g||$4<g)g=$4} END{print c+0, g+0}')
  [ "$c" -gt "$maxc" ] && maxc=$c; [ "$g" -gt "$maxg" ] && maxg=$g
  set -- $lo; [ "$1" -gt 0 ] && [ "$1" -lt "$minc" ] && minc=$1; [ "$2" -gt 0 ] && [ "$2" -lt "$ming" ] && ming=$2
  if { [ "$c" -ge 88 ] || [ "$g" -ge 84 ]; } && [ $hot = 0 ]; then echo "HOT: CPU core ${c}C GPU ${g}C"; hot=1; fi
done
# exceptions only: the window summary is printed only when temps ran warm or could not be read
if [ "$maxc" -eq 0 ]; then echo "watch window: no temperature samples read"
elif [ "$maxc" -ge 85 ] || [ "$maxg" -ge 82 ]; then echo "watch window temps ran warm: CPU hottest core ${minc}-${maxc}C, GPU ${ming}-${maxg}C"; fi
