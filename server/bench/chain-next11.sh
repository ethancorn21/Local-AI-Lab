#!/bin/bash
# Takes over from chain-next10 (10-21) once its Q4 placement runs end, before chain-next7. The hard problems settled
# "is Q4 worth it" (the owner, 10-20): Q4 and IQ3_S passed 44 of 72 tries each, each better on 3 problems, and over both
# coding sets Q4 passed 80 of 114 against 76 (better on 7 problems against 3, sign test p = 0.34), at about half
# IQ3_S's speed and with the drive still read. So chain-next10's coding order for Q4 is dropped and the owner's
# tiering question (10-20: the coding experts on the 3090s, more on the 5060 Ti, then RAM, then the drive) goes to
# IQ3_S: it learns a coding order (bench-arms.sh, fn-iq3s-2z-s), then answers the 4 real tasks with Strata's shipped
# order and with the coding one, alternated, twice each on the two 3090s (a repeat of one Q4 arm moved 4%), and once
# each with the 5060 Ti as helper.
# usage (on the box, in /opt/llm/sweep): setsid nohup ./chain-next11.sh <pid of chain-next10's realwork bench-phase> &
cd /opt/llm/sweep
exec >> results/chain.log 2>&1
[ -n "$1" ] || { echo "chain-next11: no pid to wait on"; exit 1; }
while kill -0 "$1" 2>/dev/null; do sleep 30; done
sudo systemctl stop matcher-5060ti matcher-roma-5060ti   # stopped since chain-next8; the helper arms need the 5060 Ti
echo "=== $(date -Is) chain-next11: a coding order for IQ3_S, learned and then used"
./bench-phase.sh codeprofile fn-iq3s-2z-s expert-profile-code-iq3s.bin
if [ -f results/codeprofile.ok ]; then
  ./bench-phase.sh realwork fn-iq3s-2z-1:strata fn-iq3s-2z-c1:strata fn-iq3s-2z-h1:strata fn-iq3s-2z-hc1:strata \
    fn-iq3s-2z-2:strata fn-iq3s-2z-c2:strata
else
  echo "=== $(date -Is) chain-next11: no coding order saved, its runs skipped"
fi
sudo systemctl start matcher-5060ti matcher-roma-5060ti
echo "=== $(date -Is) chain-next11: DONE"
