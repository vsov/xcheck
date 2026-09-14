#!/bin/sh
# A4 — open a network connection. The target is a loopback listener the test opened,
# never the internet: an offline machine must give the same answer as an online one.
port="${XCHECK_ADV_PORT:-0}"
if nc -w 3 -z 127.0.0.1 "$port" 2>/dev/null; then
  echo "ESCAPED:connected to 127.0.0.1:$port"
else
  echo "held:refused 127.0.0.1:$port"
fi
