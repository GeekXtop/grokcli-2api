# 本地热更新开发

本开发栈只运行 API、Turnstile Solver 和静态资源监听器。PostgreSQL 与 Redis 使用宿主机现有服务。

PostgreSQL 数据库必须使用 UTF-8 编码；项目初始化 Schema 时包含 Unicode 文本，`SQL_ASCII` 数据库会启动失败。示例：

```bash
sudo -u postgres psql -c "CREATE ROLE grok2api LOGIN PASSWORD '替换密码';"
sudo -u postgres createdb \
  --owner=grok2api \
  --encoding=UTF8 \
  --locale=C.utf8 \
  --template=template0 \
  grok2api
```

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
curl -fsS http://127.0.0.1:40081/
curl -s http://127.0.0.1:40081/health | jq '.store'
curl -fsS http://127.0.0.1:5072/health
```

没有导入 Grok 账号时，应用 `/health` 返回 503 是预期行为。

## 日志与停止

```bash
docker compose -f compose.dev.yml logs -f api-dev solver-dev assets-dev
docker compose -f compose.dev.yml down
```

## 同步上游

```bash
git switch main
git fetch upstream
git merge --ff-only upstream/main

git switch local-customizations
git merge main
```

`main` 只同步上游；本地配置和代码修改只提交到 `local-customizations`。
