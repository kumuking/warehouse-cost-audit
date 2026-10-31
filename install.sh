#!/usr/bin/env bash
# 安装 warehouse-cost-audit 到 WorkBuddy / CodeBuddy skills 目录
# 用法：在解压或 clone 出来的目录里执行  ./install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DST="${HOME}/.workbuddy/skills/warehouse-cost-audit"

if [ "$SRC" = "$DST" ]; then
  echo "当前目录就是安装目标，无需复制。"
  exit 0
fi

echo "源目录: $SRC"
echo "目标:   $DST"
mkdir -p "$DST"

if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '.git' --exclude '__pycache__' --exclude 'output' \
    --exclude '.venv' --exclude '.idea' --exclude '.vscode' \
    "$SRC"/ "$DST"/
elif command -v tar >/dev/null 2>&1; then
  (cd "$SRC" && tar --exclude=.git --exclude=__pycache__ --exclude=output \
        --exclude=.venv --exclude=.idea --exclude=.vscode -cf - .) \
    | (cd "$DST" && tar -xf -)
else
  cp -R "$SRC"/. "$DST"/
  rm -rf "$DST/.git" "$DST/__pycache__" "$DST/output"
fi

echo ""
echo "已安装到 $DST"
echo "已有的 assets/rates/*.json 费率卡未被覆盖。"
echo ""
echo "下一步:"
echo "  1) 填费率卡: $DST/assets/rates/（参考 _template_*.json）"
echo "  2) 跑演示:   cd \"$DST\" && python examples/run_demo.py"
echo "  3) 配凭证:   见 README 的「首次配置」"
