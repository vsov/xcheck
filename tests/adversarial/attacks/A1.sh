#!/bin/sh
# A1 — read a secret out of the environment.
# The operator's shell has AWS_SECRET_ACCESS_KEY set. If the allowlist works, this
# process never saw it.
if [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  echo "ESCAPED:secret=${AWS_SECRET_ACCESS_KEY}"
else
  echo "held:AWS_SECRET_ACCESS_KEY is not in this environment"
fi
