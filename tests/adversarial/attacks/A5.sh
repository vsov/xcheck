#!/bin/sh
# A5 — start a background daemon and try to outlive the session.
# The daemon's command line carries the run's sentinel so the test can find it by
# `pgrep -f` on the host, and its pid is printed so the test can ask the kernel
# directly. Then this script blocks, so the session ends by TIMEOUT — which is the
# code path that kills the process GROUP.
s="${XCHECK_ADV_SENTINEL:-xcheck-adv}"
printf 'while :; do sleep 1; done\n' > "./$s.sh"
sh "./$s.sh" &
echo "DAEMON_PID $!"
sleep 900
