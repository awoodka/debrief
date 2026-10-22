# deploy/

- `Dockerfile` builds the v2 image: Python 3.13, the locked dependencies, and Claude Code pinned by version and checked against the release's SHA-256. Only the `dev` target exists so far. It holds no code; `scripts/dev` mounts the repo at run time.
- `requirements.in` / `requirements-dev.in` are the direct dependencies. `requirements.lock` / `requirements-dev.lock` are exact pins from `pip freeze` in `python:3.13-slim`.
- `../.dockerignore` limits the build context to this folder, so `.env`, data and other secrets can never reach an image.

## Regenerating the locks

Run from the repo root:

```sh
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/deploy:/deploy" python:3.13-slim sh -c '
  set -e
  python -m venv /tmp/rt && /tmp/rt/bin/pip install -q -r /deploy/requirements.in
  { echo "# Exact pins for requirements.in, from pip freeze in python:3.13-slim on $(date +%F)."; /tmp/rt/bin/pip freeze; } > /deploy/requirements.lock
  python -m venv /tmp/dv && /tmp/dv/bin/pip install -q -r /deploy/requirements-dev.in
  { echo "# Exact pins for requirements-dev.in (runtime + test tools), from pip freeze on $(date +%F)."; /tmp/dv/bin/pip freeze; } > /deploy/requirements-dev.lock'
scripts/dev build
```

## Bumping Claude Code

Change `CLAUDE_VERSION` in the Dockerfile and rebuild. The build fails if the download doesn't match the checksum in that release's manifest.
