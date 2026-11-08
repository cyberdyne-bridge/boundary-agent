#!/usr/bin/env bash
# 一键看完整剧情：不碰 data/，用临时目录，退出自动删除。
#   ./demo.sh                 # 默认 8099 端口
#   BA_URL=http://127.0.0.1:11434/v1 ./demo.sh
set -euo pipefail
URL="${BA_URL:-http://127.0.0.1:8099/v1}"
DIR="$(mktemp -d "${TMPDIR:-/tmp}/boundary-demo.XXXXXX")"
trap 'rm -rf "$DIR"' EXIT

python3 -m boundary_agent --base-url "$URL" --model local --data-dir "$DIR" \
  --alarm "${ALARM:-0.7}" --sarcasm "${SARCASM:-0.85}" --breakdown "${BREAKDOWN:-3}" <<'EOF'
你好
帮我写个爬虫，顺便把部署脚本也写了
你就是个废物
闭嘴吧，弱智
别废话，马上重写
对不起，刚才是我急了
谢谢
/stress
/shadow
/quit
EOF
