# The image xcheck's container profile runs Codex CLI in. Base pinned by digest
# (node:22-slim, pulled 2026-10-17) and the CLI pinned by exact version; ca-certificates
# because the CLI's HTTPS client reads the system trust store. Build, then pin the RESULT:
#   docker build -t xcheck-codex images/codex
#   container_image=xcheck-codex@$(docker image inspect -f '{{.Id}}' xcheck-codex)
# The apt layer is not byte-reproducible across days (Debian updates the package); the
# digest you pin is the record of what you built, which is why the pin is the result's.
FROM node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @openai/codex@0.162.1 \
 && npm cache clean --force
USER node
ENTRYPOINT ["codex"]
