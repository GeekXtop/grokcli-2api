# Go Hot Reload Development Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the v2.0.1 Go main process in `compose.dev.yml` with automatic in-container compile and restart on Go source changes.

**Architecture:** Add a Dockerfile `development` target that contains the Go toolchain while keeping the default `production` target compiler-free. Extend `scripts/dev_watch.py` with an `api` mode that builds main and migration binaries under `/tmp`, promotes them only after both builds succeed, and launches the main process through the existing entrypoint.

**Tech Stack:** Docker multi-stage builds, Docker Compose, Python `unittest`, Go 1.24

## Global Constraints

- Preserve the GitHub BuildKit secret and Camoufox installation checks.
- Preserve solver and asset watcher behavior.
- Keep the common `.:/app` source bind mount.
- The production image default must not contain the Go toolchain.
- Failed Go builds must not replace or stop the last successful API process.
- Use `/tmp/grok2api-dev` and `/tmp/grok2api-migrate-dev` for promoted development binaries.

---

### Task 1: Add development image and Compose wiring

**Files:**
- Modify: `tests/test_dockerfile.py`
- Modify: `tests/test_dev_compose.py`
- Modify: `Dockerfile`
- Modify: `compose.dev.yml`

**Interfaces:**
- Produces Dockerfile targets named `development` and `production`.
- Produces Compose environment `GROK2API_MIGRATE_BIN=/tmp/grok2api-migrate-dev`.
- Starts `python /app/scripts/dev_watch.py api` in `api-dev`.

- [ ] **Step 1: Write failing Dockerfile and Compose tests**

Add tests asserting that:

```python
self.assertIn("FROM python:3.12-slim-bookworm AS runtime-base", dockerfile)
self.assertIn("FROM runtime-base AS development", dockerfile)
self.assertIn("COPY --from=go-builder /usr/local/go /usr/local/go", dockerfile)
self.assertTrue(dockerfile.rstrip().endswith("FROM runtime-base AS production"))
```

and that the resolved `api-dev` service has:

```python
self.assertEqual("development", build["target"])
self.assertEqual("go", environment["GROK2API_RUNTIME"])
self.assertEqual("/tmp/grok2api-migrate-dev", environment["GROK2API_MIGRATE_BIN"])
self.assertEqual(
    ["python", "/app/scripts/dev_watch.py", "api"],
    service["entrypoint"],
)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
GROK2API_ENV_FILE=.env.dev.example python3 -m unittest tests.test_dockerfile tests.test_dev_compose -v
```

Expected: failures because the targets and Go watcher configuration do not exist.

- [ ] **Step 3: Implement the minimal Dockerfile and Compose changes**

Name the Python stage:

```dockerfile
FROM python:3.12-slim-bookworm AS runtime-base
```

Append after the existing runtime `CMD`:

```dockerfile
FROM runtime-base AS development
COPY --from=go-builder /usr/local/go /usr/local/go
ENV PATH=/usr/local/go/bin:${PATH}

FROM runtime-base AS production
```

Update `api-dev.build`:

```yaml
target: development
```

Update the API environment and entrypoint:

```yaml
GROK2API_RUNTIME: go
GROK2API_MIGRATE_BIN: /tmp/grok2api-migrate-dev
entrypoint: ["python", "/app/scripts/dev_watch.py", "api"]
```

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2 and expect all tests to pass.

---

### Task 2: Implement Go build and hot restart watcher

**Files:**
- Modify: `tests/test_dev_watch.py`
- Modify: `scripts/dev_watch.py`
- Create: `tests/test_entrypoint.py`
- Modify: `entrypoint.sh`

**Interfaces:**
- `api_build_commands(go: str = "go") -> list[list[str]]`
- `api_command() -> list[str]`
- `build_api_binaries(commands, *, cwd=ROOT) -> bool`
- `run_api() -> int`

- [ ] **Step 1: Write failing watcher tests**

Add tests asserting:

```python
self.assertEqual(
    [
        ["go", "build", "-o", "/tmp/grok2api-dev.next", "./cmd/grok2api"],
        [
            "go", "build", "-o", "/tmp/grok2api-migrate-dev.next",
            "./cmd/grok2api-migrate",
        ],
    ],
    api_build_commands(),
)
self.assertEqual(
    ["/app/entrypoint.sh", "/tmp/grok2api-dev"],
    api_command(),
)
```

Use a temporary executable shell script as the `go` command to verify that
`build_api_binaries` returns `False` and does not promote `.next` files when a
build exits non-zero. Use another temporary script that writes both requested
outputs to verify successful promotion to the two final `/tmp` paths supplied
through injectable destination arguments.

- [ ] **Step 2: Run watcher tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_dev_watch -v
```

Expected: import failures for the new API watcher helpers.

- [ ] **Step 3: Implement build helpers and API watcher**

Add constants for final and `.next` binaries. Build both `.next` binaries with
`subprocess.run(..., check=False)`. Only after both return zero, use
`os.replace` to promote both files.

Implement `run_api()` so that it:

1. snapshots `cmd`, `internal`, `go.mod`, `go.sum`, and `.release-commit`;
2. performs the initial build;
3. starts `/app/entrypoint.sh /tmp/grok2api-dev` only after success;
4. keeps the current child running while compiling a change;
5. stops and replaces the child only after a successful build;
6. waits for a new change after build failure;
7. restarts the last successful binary if the child exits unexpectedly;
8. handles SIGTERM and SIGINT with `stop_child`.

Extend CLI validation:

```python
if len(sys.argv) != 2 or sys.argv[1] not in {"api", "solver", "assets"}:
    print("usage: dev_watch.py {api|solver|assets}", file=sys.stderr)
    return 2
```

Move explicit command selection in `entrypoint.sh` before validation of the
default `/app/bin/grok2api`. Add a regression test asserting this ordering so
the `/tmp/grok2api-dev` command remains valid under the source bind mount.

- [ ] **Step 4: Run watcher and related tests and verify GREEN**

Run:

```bash
GROK2API_ENV_FILE=.env.dev.example python3 -m unittest \
  tests.test_dev_watch tests.test_dev_compose tests.test_dockerfile -v
```

Expected: all tests pass.

---

### Task 3: Build, recreate, and verify the development stack

**Files:**
- Verify only; no planned source changes.

- [ ] **Step 1: Build the development image with authenticated GitHub API access**

Run:

```bash
export GITHUB_TOKEN="$(gh auth token)"
GROK2API_ENV_FILE=.env.dev.example docker compose -f compose.dev.yml build api-dev
```

Expected: build exits 0 and selects target `development`.

- [ ] **Step 2: Recreate the development services**

Run:

```bash
export GITHUB_TOKEN="$(gh auth token)"
docker compose -f compose.dev.yml up -d --force-recreate
docker compose -f compose.dev.yml ps
```

Expected: API and solver become healthy; assets remains running.

- [ ] **Step 3: Verify runtime and hot reload**

Capture the API watcher log, touch `internal/buildinfo/buildinfo.go`, and verify
the log contains a second successful build/start sequence while the API health
endpoint returns 200 afterward. Restore the original timestamp with a second
touch if needed; do not edit file contents.

- [ ] **Step 4: Run final verification and commit**

Run:

```bash
GROK2API_ENV_FILE=.env.dev.example python3 -m unittest \
  tests.test_dev_watch tests.test_dev_compose tests.test_dockerfile -v
go test ./internal/config ./internal/registration/client
git diff --check
git status --short
```

Then commit the scoped source and test changes:

```bash
git add Dockerfile compose.dev.yml entrypoint.sh scripts/dev_watch.py \
  tests/test_dockerfile.py tests/test_dev_compose.py tests/test_dev_watch.py \
  tests/test_entrypoint.py \
  docs/superpowers/specs/2026-07-18-go-hot-reload-dev-compose-design.md \
  docs/superpowers/plans/2026-07-18-go-hot-reload-dev-compose.md
git commit -m "fix(dev): restore Go API hot reload"
```
