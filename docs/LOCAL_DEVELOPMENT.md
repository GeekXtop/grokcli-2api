# 本地热更新开发

本开发栈只运行 API、Turnstile Solver 和静态资源监听器。PostgreSQL 与 Redis 使用宿主机现有服务。

API 默认监听 `0.0.0.0:40081`，可通过局域网地址 `http://192.168.100.105:40081/admin` 访问。Turnstile Solver 仍只监听 `127.0.0.1:5072`，不会暴露到局域网。

---

## 日常：改代码不需要重建

仓库目录挂载在容器的 `/app`，entrypoint 是 `dev_watch.py`。**改本地代码存盘即生效**，watcher 自动重启对应进程：

| 改动 | 效果 |
| --- | --- |
| `cmd/`、`internal/` 下的 Go 文件 | 容器内 watcher 复用缓存热编译、重启 API |
| Python API | Uvicorn 自动重启 |
| `turnstile-solver/*.py` | 只重启 Solver 子进程 |
| `static/js`、`static/css` | 自动运行资源构建脚本 |
| HTML | 直接从挂载仓库读取 |

**不要因为改了源码就去 `docker compose build`。** 只有 Dockerfile、`requirements*.txt` 或系统依赖变化时才需要新镜像。

## 需要新镜像时：一条命令

```bash
./scripts/g2a-update.sh
```

一键完成：同步 `upstream/main` → 合并进 `local-customizations` → 推送触发 CI → 等构建完成 → 拉取镜像并事务性替换容器 → 健康检查。上游没更新且远端已同步时会自动跳过 CI 等待，直接拉取现有镜像。

其他用法：

```bash
./scripts/g2a-update.sh --pull-only   # 不碰 git，只拉当前 dev 镜像并替换
./scripts/g2a-update.sh --no-wait     # 推送后不等 CI，稍后自己跑 --pull-only
```

它内部调用的 `./scripts/g2a-dev-pull.sh` 也可以单独用：拉取 `ghcr.io/geekxtop/grokcli-2api:dev` 并事务性替换三个容器——先用旧镜像建好停止状态的备份容器（`volumes_from` 保留数据卷），再替换、等 `/health`、`/ready` 和 solver `/health` 全通过；任何一步失败自动把旧容器改名恢复并启动，**不会删除任何数据卷**。

构建放在 CI 而不是本地，因为本机构建这套镜像太慢。镜像由 GitHub Actions 在推送 `local-customizations` 后构建。

> 仓库根目录的 `sync-upstream.ps1` 已过时——它操作的 `dev` 分支不存在，跑了只会同步 `main` 然后静默跳过。用 `g2a-update.sh` 代替。

## 同步上游（手工分解）

`g2a-update.sh` 已经封装了这些步骤，下面只在需要手工接管时参考：

```bash
git switch main
git fetch upstream
git merge --ff-only upstream/main

git switch local-customizations
git merge main
git push origin main
git push origin local-customizations
```

`main` 只同步上游；本地配置和代码修改只提交到 `local-customizations`。

## 回滚到某次提交的镜像

```bash
GROK2API_DEV_IMAGE=ghcr.io/geekxtop/grokcli-2api:sha-dev-<12位SHA> \
  ./scripts/g2a-dev-pull.sh
```

镜像值是 `sha-dev-*` 时脚本自动叠加 `compose.dev.pinned.yml`，移除 `.:/app` 挂载——三个服务都从镜像里的代码启动，**不受当前 checkout 影响，也没有热重载**。这是回滚/排查模式，查完记得跑一次不带参数的 `./scripts/g2a-dev-pull.sh` 回到日常模式。

可用标签见 [README 的 Fork 开发环境章节](../README.md#fork-开发环境ghcr--热更新)。

---

## 首次启动

PostgreSQL 数据库必须使用 UTF-8 编码；项目初始化 Schema 时包含 Unicode 文本，`SQL_ASCII` 数据库会启动失败：

```bash
sudo -u postgres psql -c "CREATE ROLE grok2api LOGIN PASSWORD '替换密码';"
sudo -u postgres createdb \
  --owner=grok2api \
  --encoding=UTF8 \
  --locale=C.utf8 \
  --template=template0 \
  grok2api
```

```bash
cp .env.dev.example .env.dev
# 编辑数据库、Redis、管理密码和 GROK2API_SECRET_KEY
./scripts/g2a-dev-pull.sh
docker compose -f compose.dev.yml logs -f
```

## 状态检查

```bash
curl -fsS http://127.0.0.1:40081/
curl -s http://127.0.0.1:40081/health | jq '.store'
curl -fsS http://127.0.0.1:5072/health
```

局域网设备使用：

```text
http://192.168.100.105:40081/admin
```

没有导入 Grok 账号时，应用 `/health` 返回 503 是预期行为。

## 日志与停止

```bash
docker compose -f compose.dev.yml logs -f api-dev solver-dev assets-dev
docker compose -f compose.dev.yml down
```

GHCR 不可用时的本地构建救援路径见 [README](../README.md#ghcr-不可用时的显式本地-overlay)。
