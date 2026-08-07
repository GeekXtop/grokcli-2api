# GHCR 开发镜像与本地热更新设计

## 背景

当前 `compose.dev.yml` 使用本地 Dockerfile 构建 `development` 镜像。镜像构建包含 Go 工具链、Python 依赖、Camoufox/Chromium 和系统运行库，首次构建耗时长、占用本地资源高。开发容器随后把整个仓库 bind mount 到 `/app`，由 `scripts/dev_watch.py` 编译并热重启 Go API。

`SubConverter-Extended` 已采用类似 fork-only GHCR workflow 的方式：GitHub Actions 构建并验证镜像，本地 Compose 只拉取镜像。此次设计把该发布方式引入 `grokcli-2api`，同时保留本地源码挂载和热修改能力。

## 目标

1. 本地默认不再执行完整 Docker build；开发环境从 GHCR 拉取预构建镜像。
2. 镜像包含重依赖、Go 工具链、Go module/build cache 和当前提交的预编译 API/迁移程序。
3. 与镜像源码一致的干净 checkout 启动时直接使用预编译程序，不触发本地 Go 编译。
4. 本地修改 Go 源码后仍由容器内 watcher 增量编译，并在成功后热重启。
5. 编译失败时继续运行上一份成功程序，不产生半成品替换。
6. 保留现有 Python solver、assets watcher、数据库/Redis 数据卷和开发端口。
7. 提供可验证的候选镜像、稳定标签和按提交回滚方式。

## 非目标

- 不在本次设计中实现“每次本地改动都提交 GitHub 并等待远程编译”的远程热编译。
- 不改变生产 `docker-compose.yml` 的运行模型或生产镜像发布流程。
- 不扩展 ARM、多架构或 Windows 开发镜像；首版只支持当前服务器所需的 `linux/amd64`。
- 不把源码、账号数据、数据库凭据或 GitHub token 写入镜像。

## 方案概览

```text
local-customizations push
          │
          ▼
 GitHub Actions
   测试 → build linux/amd64 → 候选 smoke test
          │
          ▼
 GHCR: ci-dev-<sha>  ──(通过后晋级)──► dev
                                      │
                     docker compose pull/up
                                      │
                                      ▼
             开发镜像 + ./:/app 源码挂载
                       │
             ┌─────────┴─────────┐
             │                   │
       digest 相同          digest 不同/源码修改
       使用预编译程序        watcher 增量编译
```

## 镜像结构

### 开发镜像

现有 Dockerfile 的 `development` target 继续保留，但改为由 GitHub Actions 构建和发布。镜像需要新增以下稳定路径：

- `/opt/grok2api-dev/bin/grok2api`
- `/opt/grok2api-dev/bin/grok2api-migrate`
- `/opt/grok2api-dev/source.digest`

这些路径不能放在 `/app` 下，因为开发 Compose 会把本地仓库挂载到 `/app` 并遮盖镜像内容。

Go builder 阶段使用固定的 module cache 和 build cache 路径，并把它们复制到 development target：

- `GOMODCACHE=/go/pkg/mod`
- `GOCACHE=/go/cache`
- `GOPATH=/go`

这样本地源码不一致时的首次增量编译仍可复用镜像内缓存。

### 源码指纹

新增可复用的指纹脚本 `scripts/dev_source_fingerprint.py`。指纹输入按稳定路径排序，并对路径和文件内容做 SHA-256：

- `cmd/**/*.go`
- `internal/**/*.go`
- `go.mod`
- `go.sum`
- `.release-commit`

构建镜像时在 `COPY . /app` 之后计算指纹并写入 `/opt/grok2api-dev/source.digest`。容器运行时对 bind-mounted `/app` 使用同一算法计算本地指纹。静态资源、Python sidecar 和 solver 不参与 Go 二进制指纹。

## 开发 watcher 行为

修改 `scripts/dev_watch.py` 的 API 模式，保留现有 solver/assets 模式。

### 启动路径

1. 计算本地 Go 源码指纹。
2. 如果等于 `/opt/grok2api-dev/source.digest`，直接使用镜像内预编译程序；迁移程序也从同一目录选择。
3. 如果不等，执行现有双构建流程，把结果写入 `/tmp/grok2api-dev.next` 和 `/tmp/grok2api-migrate-dev.next`。
4. 两个构建都成功后才原子提升到当前临时路径并启动 API。
5. 首次脏源码构建失败时不启动旧的、与源码不匹配的预编译程序，而是记录失败并等待下一次源码变化；这样不会静默运行错误版本。

### 运行中修改

- watcher 继续监视 `cmd/`、`internal/`、`go.mod`、`go.sum`、`.release-commit`。
- 变更触发 debounce 后在当前 API 进程仍运行时编译。
- 双构建全部成功后停止旧进程、原子替换并启动新进程。
- 任一构建失败时保留旧进程和旧二进制，等待下一次变更。
- SIGTERM/SIGINT 行为保持现有实现。

`api_command`、迁移二进制选择和构建路径改为可注入，以便单元测试覆盖“指纹匹配跳过编译”“指纹不匹配编译”“失败保留旧进程”三条路径。

## Compose 与本地更新

### `compose.dev.yml`

- `image` 默认值为 `ghcr.io/geekxtop/grokcli-2api:dev`，允许 `GROK2API_DEV_IMAGE` 覆盖。
- 设置 `pull_policy: always`，或由更新脚本显式执行 `docker compose pull`。
- 删除 `build:` 和 `github_token` build secret。
- 保留 `.:/app`、host network、`.env.dev`、单 worker、Go API watcher、solver/assets watcher 及现有 healthcheck。
- 不挂载 Docker socket；开发热更新只依赖容器内 watcher。

### 更新脚本

新增 `scripts/g2a-dev-pull.sh`，负责：

1. 读取可选的 `GROK2API_DEV_IMAGE`。
2. 拉取开发镜像。
3. 使用 `docker compose -f compose.dev.yml up -d --force-recreate --no-build` 重建开发服务。
4. 轮询 API `/health`、`/ready` 和 solver `/health`，失败时输出容器日志并返回非零状态。

常规更新命令：

```bash
./scripts/g2a-dev-pull.sh
```

回滚命令：

```bash
GROK2API_DEV_IMAGE=ghcr.io/geekxtop/grokcli-2api:sha-dev-<commit> \
  ./scripts/g2a-dev-pull.sh
```

脚本不删除任何 PostgreSQL、Redis 或应用数据卷。

为 GHCR 首次发布准备好之前，可保留单独的 `compose.dev.local.yml` 作为明确的本地构建救援入口；它不被默认 Compose 文件引用。

## GitHub Actions 发布流程

新增 fork 专用 workflow `.github/workflows/build-fork-dev-ghcr.yml`：

1. 触发于 `local-customizations` 分支 push，并支持 `workflow_dispatch`。
2. 设置 `packages: write` 和 `contents: read` 权限。
3. 运行 Python 回归、`go vet ./...`、`go test ./...` 和 `go test -race ./...`。
4. 使用 Docker Buildx 构建 Dockerfile 的 `development` target，仅 `linux/amd64`。
5. 通过 BuildKit secret 提供 `GITHUB_TOKEN` 给 Camoufox fetch；不得把 token 写入层或环境持久化文件。
6. 推送候选标签 `ci-dev-<12-char-sha>`，并使用候选 digest 做镜像 smoke test。
7. 查询 GitHub API，确认远端 `local-customizations` 仍指向本次 SHA，避免旧 workflow 覆盖新提交。
8. 仅在以上检查通过后，把候选 digest 晋级为：
   - `dev`
   - `local-customizations-dev`
   - `sha-dev-<12-char-sha>`
9. 使用 GHCR registry cache，减少后续 GitHub 构建时间。

GHCR 包首轮发布后设为 public，服务器不需要 registry 登录。镜像标签 `dev` 是可变开发标签，提交标签用于审计和回滚。

## Smoke test 与验证

候选镜像验证至少包括：

- 镜像包含两个 `/opt/grok2api-dev/bin` 可执行文件和 `source.digest`。
- 镜像的 `development` target 含 Go 工具链，`production` target 不含 Go compiler。
- 以无源码挂载方式启动 watcher 时，指纹匹配路径不执行 `go build`。
- 以源码挂载方式启动时，API `/health` 与 `/ready` 返回 200、版本正确。
- solver `/health` 返回 200。
- 修改一个 Go 文件时间戳后，watcher 记录一次成功重编译和重启，健康检查恢复 200。
- 人为注入失败构建命令时，旧 API 进程保持运行。
- `docker compose config` 不依赖本地 `GITHUB_TOKEN`，且没有 `build:`。

## 兼容性、风险与回滚

### 镜像拉取失败

`g2a-dev-pull.sh` 在拉取失败时不执行破坏性清理，当前已运行容器继续保持；用户可以稍后重试或使用旧的 SHA 标签。

### 镜像与源码不一致

指纹不匹配时禁止直接运行镜像预编译程序，改为本地编译。编译失败则 API 不启动（首次启动）或保持上一版本（运行中修改）。

### GHCR 标签漂移

开发日常使用 `dev`；问题定位和回滚使用 `sha-dev-*`。更新脚本打印实际镜像 digest，便于核对。

### 公共镜像安全

开发镜像只包含公开源码和依赖。账号数据、`.env.dev`、数据库内容、代理凭据和 GitHub token 均通过运行时挂载/环境注入，不进入镜像层。

## 成功标准

完成后应满足：

1. 在干净 `local-customizations` checkout 执行 `./scripts/g2a-dev-pull.sh` 不触发本地 Docker build，API 使用镜像预编译程序启动。
2. 修改 `cmd/` 或 `internal/` 中的 Go 文件后，容器内 watcher 能完成增量编译并热重启。
3. 编译失败不会替换当前可用程序。
4. GitHub Actions 只晋级经过测试的 GHCR digest。
5. 本地 `docker compose ps` 显示 API 和 solver healthy，已有数据卷保持不变。
6. 可用 SHA 标签回滚到任意已发布开发镜像。
