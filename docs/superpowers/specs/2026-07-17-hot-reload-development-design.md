# Hot-Reload Development Environment Design

## Goal

Provide a fast, self-hosted development environment for this fork where changes to the Python API, admin static assets, and Turnstile registration solver take effect without rebuilding the Docker image. The existing PostgreSQL and Redis services on the Linux host remain the shared stores.

## Chosen Approach

Build the repository Docker image once to obtain the complete Python, browser, Camoufox, and Patchright dependency set. Run development services from that image while bind-mounting the repository at `/app`.

Source-only edits do not rebuild the image. Rebuilds are required only when changing the Dockerfile, Python requirement files, browser dependencies, or when validating a newly compiled Go binary.

## Git Branch Strategy

The fork uses two long-lived branches with separate responsibilities:

- `main` is a clean mirror of `upstream/main` and contains no local configuration or code changes.
- `local-customizations` contains the development environment and all personal code changes.

`origin` points to `GeekXtop/grokcli-2api`, while `upstream` points to `HM2899/grokcli-2api`. Pushes to the `upstream` remote are disabled locally to prevent accidental writes.

Upstream updates are integrated with merge commits rather than rebasing the customization branch. This makes conflicts explicit, preserves the history of local changes, and allows Git `rerere` to reuse previous conflict resolutions.

The normal update flow is:

```bash
git switch main
git fetch upstream
git merge --ff-only upstream/main

git switch local-customizations
git merge main
```

After verification, either branch may be pushed to the fork explicitly. Development work never lands directly on `main`.

## Architecture

The development Compose stack contains three services using host networking:

1. `api-dev` runs the Python runtime with Uvicorn reload enabled and one worker.
2. `solver-dev` runs the Turnstile solver under a file watcher and restarts it when solver Python files change.
3. `assets-dev` watches admin JavaScript and CSS sources and reruns the hashed-asset builder.

All services bind-mount the same repository. The API connects to host PostgreSQL and Redis through `127.0.0.1`. The solver listens on host loopback port `5072`, and the API reaches it through the same address. Inline solver startup is disabled in the API container to prevent duplicate solver processes.

## Configuration

Development settings live in a dedicated, gitignored `.env.dev` file derived from a committed `.env.dev.example` template.

Required behavior:

- `GROK2API_RUNTIME=python`
- `GROK2API_RELOAD=1`
- `GROK2API_WORKERS=1`
- `GROK2API_STORE_BACKEND=hybrid`
- `GROK2API_REQUIRE_SHARED_STORES=1`
- PostgreSQL and Redis URLs point at `127.0.0.1`
- `GROK2API_INLINE_SOLVER=0` in container services
- API and solver listen only on `127.0.0.1` by default

Secrets are never committed. The Redis key prefix is configurable so the development instance does not collide with another application using the same Redis database.

## Hot-Reload Behavior

Python application changes trigger Uvicorn's normal reload mechanism. Development runs a single worker because Uvicorn cannot combine reload with multi-worker execution.

Turnstile solver changes restart only the solver process. This keeps API requests available during solver iteration and avoids restarting background account maintenance for every solver edit.

Admin JavaScript and CSS changes rerun `scripts/build_admin_assets.py`. The watcher observes source directories only; it must not watch generated `static/dist` files or rewritten admin HTML, avoiding rebuild loops.

HTML changes are served directly from the mounted repository and do not require an image rebuild.

## Go Runtime Scope

The default development loop remains Python because the project itself marks the Go runtime as staged. Go source changes are initially validated by rebuilding or running targeted Go commands. A dedicated Go watcher is deferred until sustained Go-runtime development makes it useful.

## Failure Handling

- The API fails closed when PostgreSQL or Redis is unreachable.
- The API and solver are separate services, so a solver crash does not terminate the API container.
- Compose restarts failed long-running development services unless explicitly stopped.
- The solver uses loopback-only networking so it is not exposed publicly.
- The API container health check uses `/`, because `/health` intentionally returns HTTP 503 before a Grok account is imported.

## Developer Workflow

Initial setup:

```bash
cp .env.dev.example .env.dev
docker compose -f compose.dev.yml build
docker compose -f compose.dev.yml up
```

Normal source iteration uses the already-built image:

```bash
docker compose -f compose.dev.yml up
```

Rebuild after dependency or Dockerfile changes:

```bash
docker compose -f compose.dev.yml build
docker compose -f compose.dev.yml up
```

## Verification

The implementation is complete when:

1. `docker compose -f compose.dev.yml config` succeeds.
2. The API can connect to the host PostgreSQL and Redis services.
3. Editing a Python API file causes an API reload without rebuilding the image.
4. Editing a Turnstile solver Python file restarts only the solver service process.
5. Editing an admin JS or CSS source regenerates `static/dist` assets.
6. No PostgreSQL, Redis, or duplicate inline solver container is created.
7. The API and solver are reachable only through host loopback by default.
