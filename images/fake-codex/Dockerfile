# The offline stand-in for the Codex image: same entrypoint name, same `exec --json`
# event shapes, no network and no model. Base pinned by digest (python:3.12-slim,
# pulled 2026-10-17); the non-root user is overridden by xcheck's `--user uid:gid`.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
COPY fake_codex.py /usr/local/bin/codex
RUN chmod 0755 /usr/local/bin/codex
USER 65532:65532
ENTRYPOINT ["codex"]
