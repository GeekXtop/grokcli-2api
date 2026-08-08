# scripts/

运维与 Python sidecar 脚本。公开 API / 管理台主路径已迁到 Go。

## 构建 / 运维

| 路径 | 说明 |
|------|------|
| `build_admin_assets.py` | 管理台静态资源打包（`static/js` → `static/dist`） |
| `upgrade_from_file_backend.sh` | file 后端 → hybrid（PG/Redis）升级迁移 |
| `smoke_go_messages.sh` | Go messages 冒烟 |
| `g2a-dev-pull.sh` | 拉取 GHCR 开发镜像、无构建强制重建开发服务并检查 API/solver 健康；失败时保留容器和数据卷 |
| `ci/smoke_dev_image.sh` | 在 development 镜像内校验预编译 Go 程序、源码指纹和编译缓存 |

### GHCR 开发镜像更新

Fork 开发默认入口是：

```bash
./scripts/g2a-dev-pull.sh
```

脚本使用 `compose.dev.yml` 拉取 `GROK2API_DEV_IMAGE`（默认
`ghcr.io/geekxtop/grokcli-2api:dev`），随后执行
`docker compose ... up -d --force-recreate --no-build`，并轮询 API `/health`、`/ready`
及 solver `/health`。拉取或健康检查失败时会打印 `ps`/`logs` 诊断，不执行
`docker compose down`、`volume prune` 或带 `-v` 的卷删除操作；事务只清理临时候选容器。可用固定的
`sha-dev-<commit>` 标签重试或回滚。

`GROK2API_DEV_IMAGE` 由 Compose 插值，可由 shell 或 project environment 覆盖：

```bash
GROK2API_DEV_IMAGE=ghcr.io/geekxtop/grokcli-2api:sha-dev-<commit> \
  ./scripts/g2a-dev-pull.sh
```

使用 `sha-dev-*` 时，脚本会自动叠加 `compose.dev.pinned.yml` 并移除 `.:/app` 源码挂载，
所以回滚实际运行的是 SHA 镜像内的代码（image-only，无当前 checkout 热更新）。默认
`dev` 标签不使用该 overlay，保留源码挂载与 Go watcher。脚本通过独立的
`compose.dev.backup.yml` 事务 overlay 保存旧镜像/卷引用；启动或健康检查失败时只移除候选
容器并恢复旧容器，不执行卷删除。

GHCR 暂不可用时，显式使用本地构建 overlay：

```bash
docker compose -f compose.dev.yml -f compose.dev.local.yml build api-dev
docker compose -f compose.dev.yml -f compose.dev.local.yml up -d --force-recreate
```

开发镜像仅发布 `linux/amd64`；默认 `dev` 的源码挂载和容器内 Go watcher 仍由
`compose.dev.yml` 保持。生产升级请遵循 `docs/UPGRADE.md` 的生产章节，不要把开发标签
当作生产默认。

JSON → PG 迁移请用 Go：

```bash
go run ./cmd/grok2api-migrate
# 或镜像内
/app/bin/grok2api-migrate
```

## Python sidecar（必须保留）

| 路径 | 说明 |
|------|------|
| `registration_service.py` | 注册机 + SSO 内部 HTTP（`127.0.0.1:18070`） |
| `sso_to_auth_json.py` | SSO cookie → token 设备流转换 |

相关实现：

- `grok2api/admin/sso_import.py` — SSO 导入任务
- `grok2api/upstream/grok_build_adapter.py` — 注册编排
- `turnstile-solver/` — 本地过盾
- `grok-build-auth/` — 协议注册引擎

边界说明见 `docs/ARCHITECTURE_GO_PYTHON_BOUNDARY.md` 与 `docs/PYTHON_SIDECAR.md`。

## 包结构约定

Sidecar 代码优先导入 `grok2api.*`。根目录 `sso_to_auth_json.py` 仅为兼容包装。
