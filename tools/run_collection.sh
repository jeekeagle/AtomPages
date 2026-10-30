#!/usr/bin/env bash
# AtomPages 采集推送脚本
#
# 流程：采集 → 生成缩略图 → 有变更才提交推送
# 由本机定时任务（WorkBuddy automation）每三小时调用一次。
# GitHub Actions 也会跑同一套逻辑，双保险。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python}"
SOURCE="${ATOM_SOURCE:-C:/Users/wyg/WorkBuddy/Claw}"

echo "=== AtomPages 采集 ==="
echo "仓库: $ROOT"
echo "源:   $SOURCE"

cd "$ROOT"
git pull --rebase --autostash origin main 2>/dev/null || true

"$PY" tools/collect.py --source "$SOURCE"

# 只在有变更时才重新生成缩略图（幂等）
if ! git diff --quiet -- data/modules.json 2>/dev/null || [ -z "$(ls -A thumbs 2>/dev/null)" ]; then
  echo "--- 生成缩略图"
  "$PY" tools/make_thumbs.py || echo "缩略图生成跳过（未装 playwright）"
fi

if git diff --quiet && [ -z "$(git status --porcelain --untracked-files=all | grep -v '^ M data/modules.json$')" ]; then
  echo "=== 无变更，结束 ==="
  exit 0
fi

git add -A
if git diff --cached --quiet; then
  echo "=== 无变更，结束 ==="
  exit 0
fi

COUNT=$(git diff --cached --name-only | wc -l | tr -d ' ')
STAMP=$(date '+%Y-%m-%d %H:%M')
git commit -m "chore: 自动汇集 ${STAMP}（${COUNT} 个文件）"
git push origin main

echo "=== 已推送 ==="
