#!/usr/bin/env bash
# 一键更新开发栈：同步上游 → 推送触发 CI → 等构建 → 拉取并替换容器。
#
# 镜像构建放在 GitHub Actions，本机只拉取。容器替换由 g2a-dev-pull.sh
# 事务性完成，失败会自动恢复旧容器，不会删除数据卷。
#
# 用法:
#   ./scripts/g2a-update.sh              # 完整流程
#   ./scripts/g2a-update.sh --pull-only  # 不碰 git，只拉当前 dev 镜像并替换
#   ./scripts/g2a-update.sh --no-wait    # 推送后不等 CI，稍后自己跑 --pull-only
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEV_BRANCH=local-customizations
WORKFLOW=build-fork-dev-ghcr.yml

step() { printf '\n[*] %s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
die() { printf '[X] %s\n' "$*" >&2; exit 1; }

pull_only=false
wait_ci=true
for arg in "$@"; do
  case "$arg" in
    --pull-only) pull_only=true ;;
    --no-wait) wait_ci=false ;;
    *) die "未知参数: $arg" ;;
  esac
done

cd "$ROOT_DIR"
command -v docker >/dev/null || die "缺少 docker"

if [[ "$pull_only" == false ]]; then
  command -v gh >/dev/null || die "缺少 gh（或用 --pull-only 跳过 CI 环节）"
  [[ -z "$(git status --porcelain=v1)" ]] || die "工作区不干净，先提交或 stash"
  [[ "$(git branch --show-current)" == "$DEV_BRANCH" ]] || die "请先切到 $DEV_BRANCH"
  git remote get-url upstream >/dev/null 2>&1 || die "未配置 upstream 远端"

  # gh 会把当前目录解析成 upstream 仓库，必须显式指定 fork。
  repo="$(sed -E 's#^https?://github\.com/##; s#^git@github\.com:##; s#\.git$##' \
    <<<"$(git remote get-url origin)")"

  step "同步 upstream"
  git fetch --prune upstream
  behind="$(git rev-list --count main..upstream/main)"
  if [[ "$behind" == 0 ]]; then
    ok "main 已是最新"
  else
    printf '    upstream 领先 %s 个提交\n' "$behind"
    git switch main
    git merge --ff-only upstream/main || {
      git switch "$DEV_BRANCH"
      die "main 不能 fast-forward，说明它有本地提交，需要人工处理"
    }
    git switch "$DEV_BRANCH"
    git merge main || die "合并冲突：解决后 git commit，再重新运行本脚本"
    ok "已合并上游改动"
  fi

  step "推送"
  before_push="$(git rev-parse "$DEV_BRANCH")"
  remote_head="$(git rev-parse "origin/$DEV_BRANCH" 2>/dev/null || echo none)"
  if [[ "$before_push" == "$remote_head" ]]; then
    ok "远端已是最新，跳过推送与 CI 等待"
    wait_ci=false
  else
    git push origin main
    git push origin "$DEV_BRANCH"
    ok "已推送，CI 开始构建"
  fi

  if [[ "$wait_ci" == true ]]; then
    step "等待 CI 构建"
    head_sha="$(git rev-parse HEAD)"
    run_id=''
    for _ in $(seq 1 30); do
      run_id="$(gh run list -R "$repo" --workflow "$WORKFLOW" --branch "$DEV_BRANCH" \
        --limit 10 --json databaseId,headSha \
        --jq ".[] | select(.headSha == \"$head_sha\") | .databaseId" | sed -n '1p')"
      [[ -n "$run_id" ]] && break
      sleep 2
    done
    [[ -n "$run_id" ]] || die "找不到 $head_sha 对应的 workflow run"
    printf '    run %s\n' "$run_id"
    gh run watch -R "$repo" "$run_id" --exit-status || die "CI 构建失败，容器未改动"
    ok "镜像构建完成"
  fi
fi

step "拉取镜像并替换容器"
"$SCRIPT_DIR/g2a-dev-pull.sh"

step "验证"
curl -fsS http://127.0.0.1:"${GROK2API_DEV_API_PORT:-40081}"/health >/dev/null && ok "API /health"
curl -fsS http://127.0.0.1:"${GROK2API_DEV_SOLVER_PORT:-5072}"/health >/dev/null && ok "Solver /health"
docker inspect --format '{{.Config.Image}}' grokcli-2api-api-dev
ok "更新完成"
