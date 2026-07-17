# Hot-Reload Development Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Docker-based local development stack where Python API, Turnstile solver, and admin assets update without rebuilding the image while using host PostgreSQL and Redis.

**Architecture:** Build the existing repository image once, then bind-mount the repository into three host-networked services. A standard-library watcher restarts the solver or rebuilds admin assets, while Uvicorn handles API reloads.

**Tech Stack:** Docker Compose v2+, Python 3.12 standard library, Uvicorn reload, existing Camoufox/Patchright image dependencies, PostgreSQL, Redis.

## Global Constraints

- Work only on `local-customizations`; `main` remains an unmodified mirror of `upstream/main`.
- Do not add PostgreSQL or Redis services to the development Compose stack.
- Default API and solver listeners must bind to `127.0.0.1`.
- Development API uses Python runtime, reload enabled, and exactly one worker.
- Source edits must not require an image rebuild.
- Rebuild only for Dockerfile, requirements, browser dependency, or compiled Go binary changes.
- Secrets live in ignored `.env.dev`; only `.env.dev.example` is committed.
- Do not watch generated `static/dist` or rewritten `static/admin` files.

---

### Task 1: Standard-Library Development Watcher

**Files:**
- Create: `scripts/dev_watch.py`
- Create: `tests/__init__.py`
- Create: `tests/test_dev_watch.py`

**Interfaces:**
- Produces: `snapshot(paths: Sequence[Path], suffixes: tuple[str, ...]) -> dict[str, tuple[int, int]]`
- Produces: `solver_command(env: Mapping[str, str]) -> list[str]`
- Produces: CLI modes `python scripts/dev_watch.py solver` and `python scripts/dev_watch.py assets`
- Consumes: existing `turnstile-solver/api_solver.py` and `scripts/build_admin_assets.py`

- [ ] **Step 1: Write watcher unit tests**

Create `tests/__init__.py` as an empty file and create `tests/test_dev_watch.py`:

```python
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scripts.dev_watch import snapshot, solver_command


class SnapshotTests(unittest.TestCase):
    def test_snapshot_tracks_only_requested_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("one")
            (root / "ignored.log").write_text("log")

            result = snapshot([root], (".py",))

            self.assertEqual([str(root / "a.py")], sorted(result))

    def test_snapshot_changes_after_file_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a.py"
            source.write_text("one")
            before = snapshot([root], (".py",))
            source.write_text("a longer value")
            os.utime(source, None)

            self.assertNotEqual(before, snapshot([root], (".py",)))


class SolverCommandTests(unittest.TestCase):
    def test_solver_command_uses_environment(self) -> None:
        command = solver_command(
            {
                "TURNSTILE_BROWSER_TYPE": "camoufox",
                "TURNSTILE_THREAD": "2",
                "TURNSTILE_HOST": "127.0.0.1",
                "TURNSTILE_PORT": "5073",
            }
        )

        self.assertIn("camoufox", command)
        self.assertIn("2", command)
        self.assertIn("127.0.0.1", command)
        self.assertIn("5073", command)
        self.assertEqual("api_solver.py", Path(command[1]).name)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and confirm the watcher module is missing**

Run:

```bash
python3 -m unittest tests.test_dev_watch -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.dev_watch'`.

- [ ] **Step 3: Implement the watcher**

Create `scripts/dev_watch.py` with these behaviors:

```python
#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLL_SECONDS = 0.5
DEBOUNCE_SECONDS = 0.25


def snapshot(
    paths: Sequence[Path], suffixes: tuple[str, ...]
) -> dict[str, tuple[int, int]]:
    state: dict[str, tuple[int, int]] = {}
    for root in paths:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file() or path.suffix not in suffixes:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            state[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return state


def solver_command(env: Mapping[str, str]) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "turnstile-solver" / "api_solver.py"),
        "--browser_type",
        env.get("TURNSTILE_BROWSER_TYPE", "camoufox"),
        "--thread",
        env.get("TURNSTILE_THREAD", "1"),
        "--host",
        env.get("TURNSTILE_HOST", "127.0.0.1"),
        "--port",
        env.get("TURNSTILE_PORT", "5072"),
        "--debug",
    ]


def stop_child(child: subprocess.Popen[bytes] | None) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=8)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=3)


def wait_for_change(
    paths: Sequence[Path], suffixes: tuple[str, ...], previous: dict[str, tuple[int, int]]
) -> dict[str, tuple[int, int]]:
    while True:
        time.sleep(POLL_SECONDS)
        current = snapshot(paths, suffixes)
        if current != previous:
            time.sleep(DEBOUNCE_SECONDS)
            return snapshot(paths, suffixes)


def run_solver() -> int:
    paths = [ROOT / "turnstile-solver"]
    state = snapshot(paths, (".py",))
    child: subprocess.Popen[bytes] | None = None
    stopping = False

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        stop_child(child)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while not stopping:
        print("[dev-watch] starting Turnstile solver", flush=True)
        child = subprocess.Popen(
            solver_command(os.environ), cwd=ROOT / "turnstile-solver"
        )
        while child.poll() is None and not stopping:
            time.sleep(POLL_SECONDS)
            current = snapshot(paths, (".py",))
            if current != state:
                state = current
                time.sleep(DEBOUNCE_SECONDS)
                print("[dev-watch] solver source changed; restarting", flush=True)
                stop_child(child)
                break
        if stopping:
            break
        if child.poll() is not None:
            time.sleep(1)
    return 0


def run_assets() -> int:
    paths = [ROOT / "static" / "js", ROOT / "static" / "css"]
    command = [sys.executable, str(ROOT / "scripts" / "build_admin_assets.py")]
    while True:
        print("[dev-watch] building admin assets", flush=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode != 0:
            print(f"asset build failed with exit code {result.returncode}", file=sys.stderr)
        state = snapshot(paths, (".js", ".css"))
        wait_for_change(paths, (".js", ".css"), state)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"solver", "assets"}:
        print("usage: dev_watch.py {solver|assets}", file=sys.stderr)
        return 2
    return run_solver() if sys.argv[1] == "solver" else run_assets()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run watcher tests**

Run:

```bash
python3 -m unittest tests.test_dev_watch -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Run syntax checks**

Run:

```bash
python3 -m py_compile scripts/dev_watch.py tests/test_dev_watch.py
```

Expected: exit code 0 with no output.

- [ ] **Step 6: Commit the watcher**

```bash
git add scripts/dev_watch.py tests/__init__.py tests/test_dev_watch.py
git commit -m "dev: add hot-reload process watcher"
```

---

### Task 2: Development Compose Stack and Environment Template

**Files:**
- Create: `compose.dev.yml`
- Create: `.env.dev.example`
- Modify: `.gitignore`
- Create: `tests/test_dev_compose.py`

**Interfaces:**
- Consumes: `scripts/dev_watch.py` CLI modes from Task 1
- Produces: Compose services `api-dev`, `solver-dev`, and `assets-dev`
- Produces: configurable env-file path `GROK2API_ENV_FILE`, default `.env.dev`

- [ ] **Step 1: Write Compose contract tests**

Create `tests/test_dev_compose.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DevComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        env = dict(os.environ)
        env["GROK2API_ENV_FILE"] = ".env.dev.example"
        result = subprocess.run(
            ["docker", "compose", "-f", "compose.dev.yml", "config", "--format", "json"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        cls.config = json.loads(result.stdout)

    def test_only_development_services_are_defined(self) -> None:
        self.assertEqual(
            {"api-dev", "solver-dev", "assets-dev"},
            set(self.config["services"]),
        )

    def test_all_services_use_host_networking(self) -> None:
        for service in self.config["services"].values():
            self.assertEqual("host", service["network_mode"])

    def test_api_has_reload_and_single_worker(self) -> None:
        environment = self.config["services"]["api-dev"]["environment"]
        self.assertEqual("1", environment["GROK2API_RELOAD"])
        self.assertEqual("1", environment["GROK2API_WORKERS"])
        self.assertEqual("0", environment["GROK2API_INLINE_SOLVER"])

    def test_env_example_is_not_ignored(self) -> None:
        result = subprocess.run(
            ["git", "check-ignore", ".env.dev.example"],
            cwd=ROOT,
            capture_output=True,
        )
        self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the Compose tests and confirm missing files fail**

Run:

```bash
python3 -m unittest tests.test_dev_compose -v
```

Expected: FAIL because `compose.dev.yml` does not exist.

- [ ] **Step 3: Allow the environment example in `.gitignore`**

Change the environment section to:

```gitignore
.env
.env.*
!.env.example
!.env.dev.example
```

- [ ] **Step 4: Add `.env.dev.example`**

Create the template with:

```dotenv
TZ=Asia/Shanghai

GROK2API_RUNTIME=python
GROK2API_HOST=127.0.0.1
GROK2API_PORT=3000
GROK2API_OPEN_BROWSER=0
GROK2API_RELOAD=1
GROK2API_WORKERS=1

GROK2API_ADMIN_PASSWORD=change-me
GROK2API_SECRET_KEY=change-me-long-random-value
GROK2API_REQUIRE_API_KEY=1

GROK2API_STORE_BACKEND=hybrid
GROK2API_REQUIRE_SHARED_STORES=1
DATABASE_URL=postgresql://grok2api:grok2api@127.0.0.1:5432/grok2api
REDIS_URL=redis://127.0.0.1:6379/0
GROK2API_REDIS_PREFIX=grok2api_dev

GROK2API_CAPTCHA_PROVIDER=local
CAPTCHA_PROVIDER=local
GROK2API_LOCAL_SOLVER_URL=http://127.0.0.1:5072
LOCAL_SOLVER_URL=http://127.0.0.1:5072
TURNSTILE_HOST=127.0.0.1
TURNSTILE_PORT=5072
TURNSTILE_THREAD=1
TURNSTILE_BROWSER_TYPE=camoufox
TURNSTILE_LAZY=1
TURNSTILE_IDLE_SEC=180
```

- [ ] **Step 5: Add `compose.dev.yml`**

Create the following Compose stack:

```yaml
name: grokcli-2api-dev

x-dev-common: &dev-common
  image: grokcli-2api:dev
  build:
    context: .
    dockerfile: Dockerfile
  network_mode: host
  env_file:
    - path: ${GROK2API_ENV_FILE:-.env.dev}
      required: true
  volumes:
    - .:/app
  init: true
  restart: unless-stopped

services:
  api-dev:
    <<: *dev-common
    container_name: grokcli-2api-api-dev
    environment:
      GROK2API_RUNTIME: python
      GROK2API_RELOAD: "1"
      GROK2API_WORKERS: "1"
      GROK2API_INLINE_SOLVER: "0"
    entrypoint: ["python", "/app/app.py"]
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:$${GROK2API_PORT:-3000}/ >/dev/null"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 30s

  solver-dev:
    <<: *dev-common
    container_name: grokcli-2api-solver-dev
    environment:
      GROK2API_INLINE_SOLVER: "0"
    entrypoint: ["python", "/app/scripts/dev_watch.py", "solver"]
    shm_size: 1gb
    security_opt:
      - seccomp:unconfined
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:$${TURNSTILE_PORT:-5072}/health >/dev/null"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 90s

  assets-dev:
    <<: *dev-common
    container_name: grokcli-2api-assets-dev
    entrypoint: ["python", "/app/scripts/dev_watch.py", "assets"]
```

- [ ] **Step 6: Run Compose contract tests**

Run:

```bash
python3 -m unittest tests.test_dev_compose -v
```

Expected: 4 tests pass.

- [ ] **Step 7: Validate the Compose model directly**

Run:

```bash
GROK2API_ENV_FILE=.env.dev.example docker compose -f compose.dev.yml config --quiet
```

Expected: exit code 0 with no output.

- [ ] **Step 8: Commit Compose support**

```bash
git add .gitignore .env.dev.example compose.dev.yml tests/test_dev_compose.py
git commit -m "dev: add hot-reload compose stack"
```

---

### Task 3: Developer Documentation and End-to-End Verification

**Files:**
- Create: `docs/LOCAL_DEVELOPMENT.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `compose.dev.yml` and `.env.dev.example`
- Produces: documented setup, normal iteration, rebuild, logs, and upstream-sync commands

- [ ] **Step 1: Write `docs/LOCAL_DEVELOPMENT.md`**

Document these exact workflows:

````markdown
# 本地热更新开发

本开发栈只运行 API、Turnstile Solver 和静态资源监听器。PostgreSQL 与 Redis 使用宿主机现有服务。

## 首次启动

```bash
cp .env.dev.example .env.dev
# 编辑数据库、Redis、管理密码和 GROK2API_SECRET_KEY
docker compose -f compose.dev.yml build
docker compose -f compose.dev.yml up -d
docker compose -f compose.dev.yml logs -f
```

## 日常修改

- Python API：Uvicorn 自动重启。
- `turnstile-solver/*.py`：只重启 Solver 子进程。
- `static/js`、`static/css`：自动运行资源构建脚本。
- HTML：直接从挂载仓库读取。

源码修改后不要执行 `docker compose build`。只有 Dockerfile、requirements 或 Go 编译产物变化时才重建。

## 状态检查

```bash
curl -fsS http://127.0.0.1:3000/
curl -s http://127.0.0.1:3000/health | jq '.store'
curl -fsS http://127.0.0.1:5072/health
```

没有导入 Grok 账号时，应用 `/health` 返回 503 是预期行为。

## 同步上游

```bash
git switch main
git fetch upstream
git merge --ff-only upstream/main
git switch local-customizations
git merge main
```
````

- [ ] **Step 2: Link the guide from `README.md`**

Add a short local-development note near the existing development section:

```markdown
> Fork 本地定制开发：使用宿主机 PostgreSQL/Redis，并分别热更新 API、注册机和管理台资源，参见 [`docs/LOCAL_DEVELOPMENT.md`](docs/LOCAL_DEVELOPMENT.md)。
```

- [ ] **Step 3: Run all local development tests**

Run:

```bash
python3 -m unittest tests.test_dev_watch tests.test_dev_compose -v
```

Expected: 7 tests pass.

- [ ] **Step 4: Run repository formatting and syntax checks for changed files**

Run:

```bash
python3 -m py_compile scripts/dev_watch.py tests/test_dev_watch.py tests/test_dev_compose.py
git diff --check main...HEAD
```

Expected: both commands exit 0.

- [ ] **Step 5: Build the development image**

Run:

```bash
GROK2API_ENV_FILE=.env.dev.example docker compose -f compose.dev.yml build
```

Expected: image `grokcli-2api:dev` builds successfully.

- [ ] **Step 6: Verify services start against host stores**

After creating a real `.env.dev`, run:

```bash
docker compose -f compose.dev.yml up -d
docker compose -f compose.dev.yml ps
docker compose -f compose.dev.yml logs --tail=100 api-dev solver-dev assets-dev
```

Expected: all three services are running; API logs report Redis and PostgreSQL as `ok`; solver listens on loopback port 5072; assets watcher completes an initial build and remains running.

- [ ] **Step 7: Verify each hot-reload path**

Run:

```bash
touch grok2api/config.py
sleep 2
docker compose -f compose.dev.yml logs --since=10s api-dev

touch turnstile-solver/browser_configs.py
sleep 2
docker compose -f compose.dev.yml logs --since=10s solver-dev

touch static/js/utils.js
sleep 2
docker compose -f compose.dev.yml logs --since=10s assets-dev
```

Expected:

- API logs show Uvicorn detecting a Python file change and reloading.
- Solver logs contain `[dev-watch] solver source changed; restarting` followed by a new solver start.
- Assets logs contain `[dev-watch] building admin assets` and successful `built` lines.

- [ ] **Step 8: Ensure verification did not change tracked content**

Run:

```bash
git diff --check
git status --short
```

Expected: no unexpected generated or source-content changes beyond the planned documentation edits.

- [ ] **Step 9: Commit documentation**

```bash
git add README.md docs/LOCAL_DEVELOPMENT.md
git commit -m "docs: document local hot-reload workflow"
```
