# GHCR 开发镜像与本地热更新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `local-customizations` 分支通过 GitHub Actions 构建并发布 GHCR 开发镜像，本地只拉取镜像，同时保留源码挂载和容器内 Go 热更新。

**Architecture:** GitHub Actions 在 `linux/amd64` 上运行测试并构建 Dockerfile 的 `development` target，先推送 `ci-dev-<sha>` 候选标签，smoke test 通过后用同一个 digest 晋级为 `dev`、分支开发标签和 SHA 回滚标签。开发 Compose 默认使用 GHCR 镜像并挂载 `.:/app`；watcher 用镜像中的源码指纹和预编译二进制快速启动，只有源码不一致时才使用镜像内 Go/module/build cache 增量编译。显式的 `compose.dev.local.yml` 保留本地构建救援路径。

**Tech Stack:** Dockerfile multi-stage build, Docker Compose v2, GitHub Actions/Buildx/GHCR, Python 3.12 `unittest`, Go 1.24, POSIX shell。

## Global Constraints

- 开发镜像只发布 `linux/amd64`；本计划不增加 ARM 或 Windows 变体。
- 默认开发镜像为 `ghcr.io/geekxtop/grokcli-2api:dev`，可由 `GROK2API_DEV_IMAGE` 覆盖。
- 源码指纹只覆盖 `cmd/**/*.go`、`internal/**/*.go`、`go.mod`、`go.sum`、`.release-commit`，路径按稳定顺序参与 SHA-256。
- 预编译产物必须位于 `/opt/grok2api-dev/bin/grok2api`、`/opt/grok2api-dev/bin/grok2api-migrate`，指纹位于 `/opt/grok2api-dev/source.digest`；不得放在会被 `.:/app` 遮盖的目录。
- development target 必须带 Go toolchain、`GOPATH=/go`、`GOMODCACHE=/go/pkg/mod`、`GOCACHE=/go/cache`；production target 不得带 Go compiler。
- 首次源码不一致且构建失败时不能启动过期预编译程序；运行中构建失败时保留当前成功进程和二进制。
- workflow 只向 `origin` 对应的 fork 发布，不修改 disabled 的 upstream；GHCR token 只能作为 BuildKit secret 使用。
- 所有实现任务先写失败测试，再写最小实现；每个任务完成后运行该任务列出的测试并单独提交。

## Scope boundaries

- 不实现“每次本地改动都提交 GitHub 并等待远程编译”的远程热编译；本地源码修改仍由容器内 watcher 处理。
- 不改变生产 `docker-compose.yml`、生产镜像标签或生产发布 workflow 的运行模型。
- 不把源码凭据、账号数据、数据库内容或 GitHub token 写入镜像层；token 只在 Camoufox/BuildKit secret 挂载期间可见。

## 文件地图

- `scripts/dev_source_fingerprint.py`：唯一的源码文件收集、排序和 SHA-256 算法，同时提供命令行输出。
- `tests/test_dev_source_fingerprint.py`：覆盖文件选择、路径排序、内容/路径变化和 CLI 输出。
- `Dockerfile`：为 Go 构建设置固定缓存，保留生产二进制，并把 development 专用缓存、预编译程序和指纹放到 `/opt/grok2api-dev`。
- `tests/test_dockerfile.py`：以文本断言验证 target、缓存、产物路径和生产隔离。
- `scripts/dev_watch.py`：复用匹配的预编译程序；不匹配时双构建并原子切换。
- `tests/test_dev_watch.py`：覆盖预编译匹配/不匹配、启动环境、失败保留旧进程和进程组行为。
- `compose.dev.yml`：默认 GHCR 拉取配置，保留三个开发服务和源码挂载。
- `compose.dev.local.yml`：显式本地构建 overlay，仅在 GHCR 不可用时使用。
- `scripts/g2a-dev-pull.sh`：拉取、强制重建开发容器并轮询健康状态，不删除数据卷。
- `tests/test_dev_compose.py`、`tests/test_dev_pull.py`：验证 Compose 合并结果和更新脚本契约。
- `.github/workflows/build-fork-dev-ghcr.yml`：fork 分支测试、候选镜像构建、smoke test 和 digest 晋级。
- `scripts/ci/smoke_dev_image.sh`：在镜像内检查 Go 工具链、缓存、二进制和指纹。
- `tests/test_dev_workflow.py`：静态验证 workflow 权限、触发器、平台、标签和竞态保护。
- `README.md`、`scripts/README.md`、`.env.dev.example`、`docs/UPGRADE.md`：记录拉取、热改、SHA 回滚和本地救援流程。
- `tests/test_dev_docs.py`：确保用户文档包含可复制的命令、镜像覆盖变量和平台限制。

---

### Task 1: 源码指纹和 Dockerfile 开发产物

**Files:**
- Create: `scripts/dev_source_fingerprint.py`
- Create: `tests/test_dev_source_fingerprint.py`
- Modify: `Dockerfile`
- Modify: `tests/test_dockerfile.py`

**Interfaces:**
- Produces `source_files(root: Path) -> list[Path]`，返回相对于 `root` 的、按 POSIX 路径排序的输入文件列表。
- Produces `source_digest(root: Path) -> str`，对每个相对路径和文件字节按 `path + NUL + content + NUL` 顺序做 SHA-256，缺失的可选文件不加入输入。
- CLI `python scripts/dev_source_fingerprint.py ROOT` 只向 stdout 打印一行十六进制 digest，错误路径返回非零。
- Docker development target 产出 `/opt/grok2api-dev/bin/grok2api`、`/opt/grok2api-dev/bin/grok2api-migrate` 和 `/opt/grok2api-dev/source.digest`。

- [ ] **Step 1: 写源码指纹失败测试**

```python
def test_source_files_are_relative_sorted_and_filtered(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "cmd").mkdir()
        (root / "internal").mkdir()
        (root / "cmd/z.go").write_text("z")
        (root / "cmd/a.go").write_text("a")
        (root / "internal/x.go").write_text("x")
        (root / "other.go").write_text("ignored")
        (root / "go.mod").write_text("module example")
        (root / ".release-commit").write_text("abc\n")
        self.assertEqual(
            [Path(".release-commit"), Path("cmd/a.go"), Path("cmd/z.go"),
             Path("go.mod"), Path("internal/x.go")], source_files(root)
        )

def test_digest_changes_for_content_and_relative_path(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); (root / "cmd").mkdir()
        source = root / "cmd/main.go"; source.write_text("package main")
        first = source_digest(root)
        source.write_text("package main\n")
        self.assertNotEqual(first, source_digest(root))
        source.rename(root / "cmd/renamed.go")
        self.assertNotEqual(first, source_digest(root))

def test_cli_prints_digest(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); (root / "cmd").mkdir()
        (root / "cmd/main.go").write_text("package main")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/dev_source_fingerprint.py"), str(root)],
            text=True, capture_output=True, check=True,
        )
        self.assertRegex(result.stdout, r"^[0-9a-f]{64}\n$")
```

- [ ] **Step 2: 运行新测试确认失败**

Run: `python3 -m unittest -v tests.test_dev_source_fingerprint`

Expected: FAIL with an import/attribute error because the module does not yet expose `source_files` and `source_digest`.

- [ ] **Step 3: 实现确定性的指纹脚本**

```python
from hashlib import sha256
from pathlib import Path
import sys

PATTERNS = ("cmd/**/*.go", "internal/**/*.go", "go.mod", "go.sum", ".release-commit")

def source_files(root: Path) -> list[Path]:
    found = {path.relative_to(root) for pattern in PATTERNS
             for path in root.glob(pattern) if path.is_file()}
    return sorted(found, key=lambda path: path.as_posix())

def source_digest(root: Path) -> str:
    digest = sha256()
    for relative in source_files(root):
        digest.update(relative.as_posix().encode("utf-8")); digest.update(b"\0")
        digest.update((root / relative).read_bytes()); digest.update(b"\0")
    return digest.hexdigest()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: dev_source_fingerprint.py ROOT")
    print(source_digest(Path(sys.argv[1]).resolve()))
```

保持脚本无第三方依赖，让 Docker build、watcher、CI smoke test 和单元测试复用同一算法。

- [ ] **Step 4: 让 Go builder 生成可复制的开发产物**

在 `Dockerfile` 的 Go builder 中加入：

```dockerfile
ENV GOPATH=/go \
    GOMODCACHE=/go/pkg/mod \
    GOCACHE=/go/cache
```

在 `COPY . /app` 后创建 `/opt/grok2api-dev/bin`，把两个 builder 二进制同时复制到原有 `/app/bin`（生产入口继续使用）和新目录，并执行：

```dockerfile
RUN mkdir -p /opt/grok2api-dev/bin \
    && cp /app/bin/grok2api /opt/grok2api-dev/bin/grok2api \
    && cp /app/bin/grok2api-migrate /opt/grok2api-dev/bin/grok2api-migrate \
    && python /app/scripts/dev_source_fingerprint.py /app > /opt/grok2api-dev/source.digest
```

在 `development` target 复制 `/usr/local/go`、`/go` 和 `/opt/grok2api-dev`；`production` 只从 `runtime-base` 启动 `/app/bin/grok2api`，不得复制 Go toolchain 或 cache。保持 Camoufox 的 BuildKit secret 挂载不变。

- [ ] **Step 5: 扩展 Dockerfile 契约测试**

在 `tests/test_dockerfile.py` 增加：

```python
def test_development_bakes_cache_and_prebuilt_artifacts(self):
    dockerfile = (ROOT / "Dockerfile").read_text()
    for value in ("GOPATH=/go", "GOMODCACHE=/go/pkg/mod", "GOCACHE=/go/cache",
                  "/opt/grok2api-dev/bin/grok2api",
                  "/opt/grok2api-dev/bin/grok2api-migrate",
                  "/opt/grok2api-dev/source.digest"):
        self.assertIn(value, dockerfile)
    self.assertIn("COPY --from=go-builder /go /go", dockerfile)
    self.assertIn("COPY --from=go-builder /usr/local/go /usr/local/go", dockerfile)

def test_production_section_does_not_copy_go_toolchain(self):
    dockerfile = (ROOT / "Dockerfile").read_text()
    production = dockerfile[dockerfile.index("FROM runtime-base AS production"):]
    self.assertNotIn("/usr/local/go", production)
    self.assertNotIn("/go/pkg/mod", production)
```

- [ ] **Step 6: 运行任务测试并提交**

Run: `python3 -m unittest -v tests.test_dev_source_fingerprint tests.test_dockerfile && git diff --check`

Expected: selected tests PASS and `git diff --check` prints no diagnostics.

Commit:

```bash
git add scripts/dev_source_fingerprint.py tests/test_dev_source_fingerprint.py Dockerfile tests/test_dockerfile.py
git commit -m "feat(dev): bake source digest and cached Go artifacts"
```

---

### Task 2: watcher 复用预编译二进制

**Files:**
- Modify: `scripts/dev_watch.py`
- Modify: `tests/test_dev_watch.py`

**Interfaces:**
- Produces `prebuilt_api_paths(artifact_root: Path) -> tuple[Path, Path]`，返回主程序和迁移程序路径。
- Produces `prebuilt_source_matches(root: Path, digest_file: Path) -> bool`，读取 digest 文件并与 `source_digest(root)` 精确比较；以 `digest_file.parent / "bin"` 下的固定文件名检查两个二进制的可执行位；文件不存在、不可读或不可执行时返回 `False`。
- Produces `select_api_startup(root: Path, artifact_root: Path) -> tuple[Path, Path] | None`，匹配时返回预编译主/迁移程序，否则返回 `None`，不触发构建。
- `api_command(main_binary: Path = API_BINARY) -> list[str]` 返回 `[/app/entrypoint.sh, str(main_binary)]`。
- `api_child_env(migrate_binary: Path) -> dict[str, str]` 返回当前环境副本，并把 `GROK2API_MIGRATE_BIN` 指向迁移程序。
- `start_child(command, *, cwd, env=None)` 将可选 `env` 原样传给 `subprocess.Popen`。

- [ ] **Step 1: 写预编译路径和指纹匹配失败测试**

```python
def test_prebuilt_paths_are_stable(self):
    self.assertEqual(
        (Path("/opt/grok2api-dev/bin/grok2api"),
         Path("/opt/grok2api-dev/bin/grok2api-migrate")),
        prebuilt_api_paths(Path("/opt/grok2api-dev")),
    )

def test_prebuilt_source_matches_only_when_digest_and_exec_files_match(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); (root / "cmd").mkdir()
        (root / "cmd/main.go").write_text("package main")
        artifact = root / "artifacts"; (artifact / "bin").mkdir(parents=True)
        digest_file = artifact / "source.digest"
        digest_file.write_text(source_digest(root))
        main = artifact / "bin/grok2api"; migrate = artifact / "bin/grok2api-migrate"
        main.touch(mode=0o755); migrate.touch(mode=0o755)
        self.assertTrue(prebuilt_source_matches(root, digest_file))
        digest_file.write_text("0" * 64)
        self.assertFalse(prebuilt_source_matches(root, digest_file))
```

- [ ] **Step 2: 运行失败测试并实现可注入接口**

Run: `python3 -m unittest -v tests.test_dev_watch.ApiWatcherTests`

Expected before implementation: FAIL with missing imports or signature mismatch.

导入 `source_digest`，定义固定 artifact root；`prebuilt_source_matches` 先验证 digest 文本为 64 个小写十六进制字符，再检查两份二进制 `os.access(path, os.X_OK)`，最后比较 `source_digest(root)`。`select_api_startup` 只选择匹配且可执行的预编译文件，不调用 `go build`。

- [ ] **Step 3: 写启动分支和失败保护测试**

使用 `unittest.mock.patch` 注入 `prebuilt_source_matches`、`build_api_binaries` 和 `select_api_startup`，断言匹配时构建函数调用次数为零；脏源码构建返回 `False` 时断言 `start_child` 未被调用，并捕获日志中的 `source differs; building Go API` 和 `Go build failed; waiting for source change`。保留现有原子提升测试，并补充运行中失败时主/迁移文件内容仍为旧值。

```python
def test_matching_prebuilt_skips_go_build(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); (root / "cmd").mkdir()
        (root / "cmd/main.go").write_text("package main")
        artifact = root / "artifacts"; (artifact / "bin").mkdir(parents=True)
        (artifact / "bin/grok2api").touch(mode=0o755)
        (artifact / "bin/grok2api-migrate").touch(mode=0o755)
        (artifact / "source.digest").write_text(source_digest(root))
        with patch("scripts.dev_watch.build_api_binaries") as build:
            selected = select_api_startup(root, artifact)
        self.assertEqual(artifact / "bin/grok2api", selected[0])
        build.assert_not_called()
```

- [ ] **Step 4: 实现双构建、原子切换和运行环境**

启动时按以下顺序执行：

1. `prebuilt_api_paths(Path("/opt/grok2api-dev"))` 得到两个候选路径。
2. 指纹匹配且两个文件可执行时选择候选路径，打印 `[dev-watch] using prebuilt Go API`，通过 `api_child_env(prebuilt_migrate)` 启动。
3. 否则打印 `[dev-watch] source differs; building Go API`，调用双构建到 `/tmp/grok2api-dev.next` 和 `/tmp/grok2api-migrate-dev.next`；只有两个构建都成功才 `os.replace` 到活动路径并启动。
4. 首次构建失败时不选择预编译文件，打印 `[dev-watch] Go build failed; waiting for source change`，进入 `wait_for_change`。
5. 运行中源码变化时继续构建 next 文件；失败只打印 `[dev-watch] Go build failed; keeping current API`，不停止当前 child；成功后停止旧 child、原子提升并用新迁移路径重启。

`start_child` 的 `env` 默认 `None`；API 启动始终传入 `api_child_env(active_migrate)`，solver/assets 行为保持不变。通过 `GROK2API_MIGRATE_BIN` 覆盖 entrypoint 的迁移选择，避免预编译迁移程序被 `/app` 挂载遮盖。

- [ ] **Step 5: 更新 watcher 单测并运行**

补充 `api_command(Path("/tmp/custom"))`、`api_child_env`、不可执行预编译文件和 digest 不匹配测试；保留进程组/SIGTERM 测试。

Run: `python3 -m unittest -v tests.test_dev_watch`

Expected: all watcher tests PASS，且测试输出中没有真实 `go build` 或长期子进程。

- [ ] **Step 6: 提交 watcher 变更**

```bash
git add scripts/dev_watch.py tests/test_dev_watch.py
git commit -m "feat(dev): reuse matching prebuilt Go binaries"
```

---

### Task 3: Compose 改为 GHCR 拉取并保留本地救援

**Files:**
- Modify: `compose.dev.yml`
- Create: `compose.dev.local.yml`
- Create: `scripts/g2a-dev-pull.sh`
- Create: `tests/test_dev_pull.py`
- Modify: `tests/test_dev_compose.py`

**Interfaces:**
- `compose.dev.yml` 的共同服务 image 为 `${GROK2API_DEV_IMAGE:-ghcr.io/geekxtop/grokcli-2api:dev}`，并设置 `pull_policy: always`；三个服务继续共享 host network、`.env.dev` 和 `.:/app`。
- `compose.dev.local.yml` 将三个服务指向 `grokcli-2api:dev` 并设 `pull_policy: never`，只给 `api-dev` 添加 `build.context=.`、`build.dockerfile=Dockerfile`、`build.target=development` 及 `github_token` secret。
- `scripts/g2a-dev-pull.sh` 不 source 任何 `.env` 文件，默认检查 API `40081`、solver `5072`，失败时保留现有容器和数据卷并返回非零。

- [ ] **Step 1: 先把 Compose 断言改成目标行为**

将 `tests/test_dev_compose.py` 的构造命令改为读取 `compose.dev.yml` 的 JSON 配置，并增加：

```python
def test_default_uses_ghcr_without_build_or_secret(self):
    for service in self.config["services"].values():
        self.assertEqual("ghcr.io/geekxtop/grokcli-2api:dev", service["image"])
        self.assertEqual("always", service["pull_policy"])
        self.assertNotIn("build", service)
    self.assertNotIn("secrets", self.config)

def test_source_mount_and_watchers_remain_enabled(self):
    for service in self.config["services"].values():
        self.assertIn(".:/app", service["volumes"])
    self.assertEqual(
        ["python", "/app/scripts/dev_watch.py", "api"],
        self.config["services"]["api-dev"]["entrypoint"],
    )
```

用第二次 `docker compose -f compose.dev.yml -f compose.dev.local.yml config --format json` 验证 overlay：`api-dev` 的 build target 是 `development`，三个服务的 image 都是 `grokcli-2api:dev` 且 `pull_policy` 为 `never`，secret 来源仍是环境变量 `GITHUB_TOKEN`。

- [ ] **Step 2: 运行失败测试**

Run: `GROK2API_ENV_FILE=.env.dev.example python3 -m unittest -v tests.test_dev_compose`

Expected: FAIL because the current default file still contains `build` and `github_token`, and the local overlay does not exist.

- [ ] **Step 3: 改写默认 Compose 文件**

在 `x-dev-common` 使用：

```yaml
x-dev-common: &dev-common
  image: ${GROK2API_DEV_IMAGE:-ghcr.io/geekxtop/grokcli-2api:dev}
  pull_policy: always
  network_mode: host
  env_file:
    - path: ${GROK2API_ENV_FILE:-.env.dev}
      required: true
  volumes:
    - .:/app
  init: true
  restart: unless-stopped
```

删除默认文件的 `build:` 区块和顶层 `secrets:`；保留 API、solver、assets 的 entrypoint、healthcheck、host network、`.env.dev`、单 worker、solver/assets watcher 以及 API 的 `GROK2API_MIGRATE_BIN` 环境变量。不要加入 Docker socket volume。

- [ ] **Step 4: 添加显式本地构建 overlay**

创建 `compose.dev.local.yml`：

```yaml
services:
  api-dev:
    image: grokcli-2api:dev
    pull_policy: never
    build:
      context: .
      dockerfile: Dockerfile
      target: development
      secrets:
        - github_token
  solver-dev:
    image: grokcli-2api:dev
    pull_policy: never
  assets-dev:
    image: grokcli-2api:dev
    pull_policy: never

secrets:
  github_token:
    environment: GITHUB_TOKEN
```

这样 `docker compose -f compose.dev.yml -f compose.dev.local.yml build api-dev` 只在用户明确指定 overlay 时执行本地 build，之后三个服务从同一份本地 development image 启动。

- [ ] **Step 5: 为更新脚本写静态契约测试**

创建 `tests/test_dev_pull.py`，先不运行 Docker：

```python
class DevPullScriptTests(unittest.TestCase):
    def test_script_pulls_and_recreates_without_build(self):
        text = (ROOT / "scripts/g2a-dev-pull.sh").read_text()
        self.assertIn("docker compose", text)
        self.assertIn("pull", text)
        self.assertIn("up -d --force-recreate --no-build", text)
        self.assertNotIn("source .env.dev", text)
        self.assertNotIn("docker compose down", text)

    def test_script_has_bounded_health_loop_and_diagnostics(self):
        text = (ROOT / "scripts/g2a-dev-pull.sh").read_text()
        self.assertRegex(text, r"90")
        for endpoint in ("/health", "/ready"):
            self.assertIn(endpoint, text)
        self.assertIn("docker compose ps", text)
        self.assertIn("docker compose logs", text)
```

- [ ] **Step 6: 实现安全的拉取/健康检查脚本**

创建可执行的 `scripts/g2a-dev-pull.sh`，核心流程如下：

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT_DIR/compose.dev.yml")
API_PORT="${GROK2API_DEV_API_PORT:-40081}"
SOLVER_PORT="${GROK2API_DEV_SOLVER_PORT:-5072}"

if ! (cd "$ROOT_DIR" && "${COMPOSE[@]}" pull); then
  echo "[g2a-dev-pull] image pull failed; existing containers were left untouched" >&2
  exit 1
fi
if ! (cd "$ROOT_DIR" && "${COMPOSE[@]}" up -d --force-recreate --no-build); then
  echo "[g2a-dev-pull] compose start failed" >&2
  "${COMPOSE[@]}" ps || true
  "${COMPOSE[@]}" logs --tail 80 api-dev solver-dev assets-dev || true
  exit 1
fi

for attempt in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null \
      && curl -fsS "http://127.0.0.1:${API_PORT}/ready" >/dev/null \
      && curl -fsS "http://127.0.0.1:${SOLVER_PORT}/health" >/dev/null; then
    "${COMPOSE[@]}" ps
    exit 0
  fi
  sleep 1
done

echo "[g2a-dev-pull] health check timed out after 90s" >&2
"${COMPOSE[@]}" ps || true
"${COMPOSE[@]}" logs --tail 120 api-dev solver-dev assets-dev || true
exit 1
```

脚本只使用 Compose 的 pull/up/ps/logs，不执行 `down`、`rm`、`volume prune` 或任何卷删除操作；`GROK2API_DEV_IMAGE` 由 Compose 自己展开，不读取 `.env.dev` 内容到 shell 环境。

- [ ] **Step 7: 运行 Compose、脚本测试并提交**

Run:

```bash
python3 -m unittest -v tests.test_dev_compose tests.test_dev_pull
docker compose -f compose.dev.yml -f compose.dev.local.yml config --format json >/tmp/grok2api-dev-local-config.json
git diff --check
```

Expected: 两组 Python 测试 PASS；overlay JSON 中只有 `api-dev` 有 development build；diff 检查无输出。

Commit:

```bash
git add compose.dev.yml compose.dev.local.yml scripts/g2a-dev-pull.sh tests/test_dev_compose.py tests/test_dev_pull.py
git commit -m "feat(dev): pull development image from GHCR"
```

---

### Task 4: fork 专用 GHCR workflow 和镜像 smoke test

**Files:**
- Create: `.github/workflows/build-fork-dev-ghcr.yml`
- Create: `scripts/ci/smoke_dev_image.sh`
- Create: `tests/test_dev_workflow.py`

**Interfaces:**
- Workflow 只响应 `push` 到 `local-customizations` 和 `workflow_dispatch`，使用 `packages: write`、`contents: read`，并对同一 ref 启用旧运行取消。
- 候选 tag 是 `ci-dev-<12-char-sha>`；成功晋级 `dev`、`local-customizations-dev`、`sha-dev-<12-char-sha>`，三个 tag 都引用候选 digest。
- Buildx 使用 Dockerfile `development` target、`linux/amd64`、registry cache `buildcache-dev-amd64` 和 `secrets: github_token=${{ github.token }}`。
- `scripts/ci/smoke_dev_image.sh` 在镜像内检查两个可执行文件、`source.digest`、`/usr/local/go/bin/go`、`/go/pkg/mod`、`/go/cache`，并用 `/app/scripts/dev_source_fingerprint.py /app` 验证 baked digest。

- [ ] **Step 1: 写 workflow 静态失败测试**

创建 `tests/test_dev_workflow.py`，以纯文本读取 YAML：

```python
class DevWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github/workflows/build-fork-dev-ghcr.yml").read_text()

    def test_trigger_permissions_and_concurrency(self):
        self.assertIn("branches: [local-customizations]", self.text)
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("packages: write", self.text)
        self.assertIn("contents: read", self.text)
        self.assertIn("cancel-in-progress: true", self.text)

    def test_build_and_promotion_contract(self):
        for value in ("target: development", "platforms: linux/amd64",
                      "ci-dev-", "local-customizations-dev", "sha-dev-",
                      "buildcache-dev-amd64", "github_token=${{ github.token }}",
                      "smoke_dev_image.sh"):
            self.assertIn(value, self.text)
        self.assertIn("git/ref/heads/local-customizations", self.text)
        self.assertIn("imagetools create", self.text)
```

- [ ] **Step 2: 运行失败测试**

Run: `python3 -m unittest -v tests.test_dev_workflow`

Expected: FAIL because the fork workflow and smoke script do not exist.

- [ ] **Step 3: 实现镜像 smoke script**

创建 `scripts/ci/smoke_dev_image.sh` 并设为可执行：

```bash
#!/usr/bin/env bash
set -euo pipefail

test -x /opt/grok2api-dev/bin/grok2api
test -x /opt/grok2api-dev/bin/grok2api-migrate
test -s /opt/grok2api-dev/source.digest
test -x /usr/local/go/bin/go
test -d /go/pkg/mod
test -d /go/cache
baked="$(tr -d '[:space:]' </opt/grok2api-dev/source.digest)"
actual="$(python /app/scripts/dev_source_fingerprint.py /app)"
test "$baked" = "$actual"
echo "development image smoke test passed: $baked"
```

脚本不启动数据库、solver 或 API，避免 smoke test 依赖外部状态；它只验证镜像层中开发 watcher 必需的文件和指纹一致性。

- [ ] **Step 4: 实现候选构建 workflow**

workflow 使用 `actions/checkout@v4`，随后运行 `python3 -m unittest discover -v`、`go vet ./...`、`go test ./...`、`go test -race ./...`。Docker 步骤使用 `docker/setup-buildx-action@v3`、`docker/login-action@v3` 和 `docker/build-push-action@v6`，关键配置：

```yaml
with:
  context: .
  file: Dockerfile
  target: development
  platforms: linux/amd64
  push: true
  tags: ${{ env.IMAGE }}:ci-dev-${{ env.SHA12 }}
  secrets: |
    github_token=${{ github.token }}
  cache-from: type=registry,ref=${{ env.IMAGE }}:buildcache-dev-amd64
  cache-to: type=registry,ref=${{ env.IMAGE }}:buildcache-dev-amd64,mode=max
```

将 build action 的 `digest` 输出保存为 job 环境变量；用 `docker run --rm --entrypoint /app/scripts/ci/smoke_dev_image.sh "$IMAGE@$DIGEST"` 执行 smoke test。随后以 `gh api repos/${GITHUB_REPOSITORY}/git/ref/heads/local-customizations --jq .object.sha` 读取远端分支头，只在它仍等于 `${GITHUB_SHA}` 时继续。

- [ ] **Step 5: 用候选 digest 原子晋级标签**

登录 GHCR 后执行以下命令，禁止重新 build：

```bash
docker buildx imagetools create \
  --tag "$IMAGE:dev" \
  --tag "$IMAGE:local-customizations-dev" \
  --tag "$IMAGE:sha-dev-$SHA12" \
  "$IMAGE@$DIGEST"
```

workflow 在 `env` 中把 `IMAGE` 固定为小写 `ghcr.io/geekxtop/grokcli-2api`，并在 job 的 `concurrency` 中使用 `${{ github.ref }}`；任何测试、smoke 或分支头检查失败都不会执行晋级命令。

- [ ] **Step 6: 运行 workflow 契约和 shell 语法测试并提交**

Run:

```bash
python3 -m unittest -v tests.test_dev_workflow
bash -n scripts/ci/smoke_dev_image.sh
git diff --check
```

Expected: workflow tests PASS，shell 语法检查退出码为 0，diff 检查无输出。

Commit:

```bash
git add .github/workflows/build-fork-dev-ghcr.yml scripts/ci/smoke_dev_image.sh tests/test_dev_workflow.py
git commit -m "ci(dev): publish tested development image to GHCR"
```

---

### Task 5: 文档、环境变量和本地救援说明

**Files:**
- Modify: `README.md`
- Modify: `scripts/README.md`
- Modify: `.env.dev.example`
- Modify: `docs/UPGRADE.md`
- Create: `tests/test_dev_docs.py`

**Interfaces:**
- 文档公开 `./scripts/g2a-dev-pull.sh` 作为默认开发更新入口。
- 文档公开 `GROK2API_DEV_IMAGE`、`dev`/SHA 标签、仅 `linux/amd64`、本地 Go 热修改和显式本地构建 overlay。
- 文档明确更新脚本不删除数据卷，并给出失败时的诊断和 SHA 回滚命令。

- [ ] **Step 1: 写文档契约失败测试**

创建 `tests/test_dev_docs.py`：

```python
class DevDocumentationTests(unittest.TestCase):
    def test_readme_has_pull_hot_reload_and_rescue_commands(self):
        text = (ROOT / "README.md").read_text()
        for value in ("./scripts/g2a-dev-pull.sh", "GROK2API_DEV_IMAGE",
                      "sha-dev-<commit>", "linux/amd64",
                      "compose.dev.local.yml", "--no-build"):
            self.assertIn(value, text)

    def test_upgrade_doc_warns_about_volumes_and_has_rollback(self):
        text = (ROOT / "docs/UPGRADE.md").read_text()
        self.assertIn("不删除", text)
        self.assertIn("sha-dev-", text)
        self.assertIn("g2a-dev-pull.sh", text)

    def test_env_example_declares_dev_image_override(self):
        text = (ROOT / ".env.dev.example").read_text()
        self.assertIn("GROK2API_DEV_IMAGE=", text)
        self.assertIn("GROK2API_RELOAD=1", text)
```

- [ ] **Step 2: 运行失败测试**

Run: `python3 -m unittest -v tests.test_dev_docs`

Expected: FAIL because the existing README describes production GHCR tags but not the fork development pull workflow.

- [ ] **Step 3: 更新 README 开发章节**

增加“Fork 开发环境（GHCR + 热更新）”章节，包含：

```bash
cp .env.dev.example .env.dev
./scripts/g2a-dev-pull.sh
docker compose -f compose.dev.yml ps
docker compose -f compose.dev.yml logs -f api-dev

GROK2API_DEV_IMAGE=ghcr.io/geekxtop/grokcli-2api:sha-dev-<commit> \
  ./scripts/g2a-dev-pull.sh

docker compose -f compose.dev.yml -f compose.dev.local.yml build api-dev
docker compose -f compose.dev.yml -f compose.dev.local.yml up -d --force-recreate
```

说明默认标签 `dev`、候选标签 `ci-dev-<sha>`、稳定分支标签 `local-customizations-dev` 和 `sha-dev-<sha>` 的用途；指出镜像只支持 `linux/amd64`，源码仍以 `.:/app` 挂载，Go 文件修改继续由容器内 watcher 热编译。明确脚本不执行 `down` 或删除 PostgreSQL/Redis/应用数据卷。

- [ ] **Step 4: 更新 scripts README、env 模板和升级文档**

在 `scripts/README.md` 的运维表加入 `g2a-dev-pull.sh` 和 `ci/smoke_dev_image.sh`；在 `.env.dev.example` 顶部加入：

```dotenv
GROK2API_DEV_IMAGE=ghcr.io/geekxtop/grokcli-2api:dev
```

保留 `GROK2API_RUNTIME=go`、`GROK2API_RELOAD=1`、单 worker 和 `40081`/`5072` 默认值；不要把 token、密码或真实数据库凭据写入模板。`docs/UPGRADE.md` 增加开发镜像升级段，说明 pull、健康检查、拉取失败不清理、SHA 回滚和显式 local overlay。

- [ ] **Step 5: 运行文档测试并提交**

Run: `python3 -m unittest -v tests.test_dev_docs && git diff --check`

Expected: all documentation tests PASS and no whitespace errors.

Commit:

```bash
git add README.md scripts/README.md .env.dev.example docs/UPGRADE.md tests/test_dev_docs.py
git commit -m "docs(dev): document GHCR pull and hot reload workflow"
```

---

### Task 6: 端到端发布、拉取和回滚验证

**Files:**
- Verify: all files from Tasks 1–5
- External state: `origin/local-customizations` and GHCR package `ghcr.io/geekxtop/grokcli-2api`

**Interfaces:**
- 只向 `origin` 推送 `local-customizations`；不得向 `upstream` 执行 push。
- 发布检查记录 workflow run、候选 digest、晋级标签和本地实际拉取的 image digest。
- 验证过程不得删除现有 Docker 数据卷；失败时保留容器供诊断。

- [ ] **Step 1: 在推送前运行本地回归**

Run:

```bash
python3 -m unittest discover -v
go vet ./...
go test ./...
go test -race ./...
test -z "$(gofmt -l cmd internal)"
git diff --check
git status --short --branch
```

Expected: all commands exit 0；工作树只包含已审阅的实现提交。

- [ ] **Step 2: 确认远端和推送目标**

Run:

```bash
git remote -v
git log -1 --oneline
git push origin local-customizations:local-customizations
```

确认目标 URL 是 `https://github.com/GeekXtop/grokcli-2api.git`；不得向 `upstream` push。记录推送 SHA。

- [ ] **Step 3: 观察并核对 GitHub Actions**

Run:

```bash
gh run list --repo GeekXtop/grokcli-2api --workflow build-fork-dev-ghcr.yml --limit 5
gh run watch <run-id> --repo GeekXtop/grokcli-2api
```

Expected: Python、Go vet、普通测试、race、development build、smoke test、远端 branch-head 检查全部成功；日志出现候选 `ci-dev-<12-char-sha>`、digest 以及 `dev`、`local-customizations-dev`、`sha-dev-<12-char-sha>` 晋级。首次发布若为 private，设置 GHCR package 为 public 后再做匿名 pull。

- [ ] **Step 4: 用默认 dev 标签启动本地开发栈**

Run:

```bash
./scripts/g2a-dev-pull.sh
docker compose -f compose.dev.yml ps
docker compose -f compose.dev.yml logs --tail 120 api-dev
curl -fsS http://127.0.0.1:40081/health
curl -fsS http://127.0.0.1:40081/ready
curl -fsS http://127.0.0.1:5072/health
docker volume ls
```

Expected: API、solver、assets 运行且健康；首次 API 日志包含 `using prebuilt Go API`，不包含初始 `building Go API`；两个 API 端点和 solver `/health` 返回 200；原有数据卷仍存在。

- [ ] **Step 5: 验证本地 Go 热修改和失败保护**

对 `internal/buildinfo/buildinfo.go` 执行 `touch`，确认 watcher 记录源码变化并出现 `source differs; building Go API`、成功重编译和 `restarting Go API`，随后检查健康端点恢复 200；源码指纹用于启动时复用预编译程序，watcher 的 mtime/size 快照仍负责运行中变更检测。再在临时工作副本注入语法错误，确认日志有 `Go build failed; keeping current API` 且旧 `/health` 仍为 200；恢复文件后确认下一次构建成功。故意的错误不得提交。

- [ ] **Step 6: 验证 SHA 回滚和卷保留**

Run:

```bash
GROK2API_DEV_IMAGE=ghcr.io/geekxtop/grokcli-2api:sha-dev-<12-char-sha> \
  ./scripts/g2a-dev-pull.sh
docker inspect grokcli-2api-api-dev --format '{{.Config.Image}}'
docker compose -f compose.dev.yml ps
docker volume ls
```

Expected: API 使用指定 SHA 镜像且健康检查通过，数据库/Redis/应用卷数量未减少；随后恢复 `:dev` 标签。

- [ ] **Step 7: 做最终证据检查并关闭计划**

Run:

```bash
python3 -m unittest discover -v
go vet ./...
go test ./...
go test -race ./...
test -z "$(gofmt -l cmd internal)"
git diff --check
git status --short --branch
```

记录 `docker compose ps`、健康端点、预编译启动日志、热更新日志、失败保护日志和 SHA 回滚结果；证据齐全后才将计划标记完成。

---

## 实施顺序和审查门

1. Task 1 先固定镜像与运行时一致的 digest；Task 2 再确保干净源码启动不调用 Go compiler。
2. Task 3 用 `docker compose config` 审查默认文件没有 build/secret，本地救援 overlay 必须显式出现。
3. Task 4 用 workflow 静态测试和 shell 语法测试拦截标签、权限、平台或 secret 错误。
4. Task 5 完成文档后才推送；Task 6 的远端发布和容器验证是最终集成门。
5. 每个任务保持独立提交；不执行 `git reset --hard`、`docker volume prune` 或针对 workspace 的递归删除。
