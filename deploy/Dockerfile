# syntax=docker/dockerfile:1
# Debrief v2 image: Python deps plus a checksum-pinned Claude Code (used only by `debrief triage`).
#
#   docker build --target dev -t debrief-v2:dev -f deploy/Dockerfile .
#
# The build context is the repo root, but .dockerignore admits only deploy/, so .env, data and
# other secrets can never be copied in. The dev target has no code in it: scripts/dev mounts the
# repo at /app at run time. A prod target that copies src/ in comes with the cutover.
FROM python:3.13-slim AS base

LABEL app=debrief-v2

ARG CLAUDE_VERSION=2.1.309
ARG CLAUDE_PLATFORM=linux-x64

# Claude Code, pinned: fetch exactly this release and verify it against the published SHA-256.
# No installer and no autoupdate (same recipe as the v1 server image).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    base="https://downloads.claude.ai/claude-code-releases/${CLAUDE_VERSION}"; \
    curl -fsSL "$base/manifest.json" -o /tmp/manifest.json; \
    sum=$(python3 -c 'import json,sys; print(json.load(open("/tmp/manifest.json"))["platforms"][sys.argv[1]]["checksum"])' "${CLAUDE_PLATFORM}"); \
    curl -fsSL "$base/${CLAUDE_PLATFORM}/claude" -o /usr/local/bin/claude; \
    echo "$sum  /usr/local/bin/claude" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/claude; \
    apt-get purge -y curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/* /tmp/manifest.json

COPY deploy/requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock

RUN useradd --uid 1000 --home-dir /home/debrief --create-home --shell /usr/sbin/nologin debrief

ENV PATH=/opt/venv/bin:$PATH \
    HOME=/home/debrief \
    DISABLE_AUTOUPDATER=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/New_York

FROM base AS dev
COPY deploy/requirements-dev.lock /tmp/requirements-dev.lock
RUN /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements-dev.lock \
 && rm /tmp/requirements-dev.lock
USER 1000:1000
WORKDIR /app
ENV PYTHONPATH=/app/src
