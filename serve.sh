#!/usr/bin/env bash
# 启动本地模型（llama.cpp / llama-server）
#
#   ./serve.sh                  # 默认 4B：单槽 4096 上下文，显存 ~3.5G，host RAM ~0.5–1.1G
#   PROFILE=small ./serve.sh    # 1.5B 备用：单槽 8192，显存 ~2G
#   PROFILE=big ./serve.sh      # 7B R1 推理：单槽 4096，显存 ~5.0G（很紧）
#   MODEL=~/models/xxx.gguf ./serve.sh
#   LLAMA_DIR=/opt/llama.cpp ./serve.sh
#
# 没有 GPU 也能跑：设 NGL=0（serve.sh 检测不到 nvidia-smi 时会自动这么做）。
#
# 注意：-np 1 是刻意的。部分 CUDA 构建在多槽批处理下会撞
# "CUDA error: an illegal memory access"；单槽压测 30 串行 + 3 并发没复现。
# 代价是 A / B / 影子三路请求会排队。你的构建如果稳定，可以 NP=4。
set -euo pipefail

LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
LLAMA_BIN="${LLAMA_BIN:-}"
if [ -z "$LLAMA_BIN" ]; then
  for cand in "$LLAMA_DIR/llama-server" "$LLAMA_DIR/build/bin/llama-server" \
              "$LLAMA_DIR/llama-b11723/llama-server" "$LLAMA_DIR/bin/llama-server"; do
    [ -x "$cand" ] && LLAMA_BIN="$cand" && break
  done
fi
[ -n "$LLAMA_BIN" ] || LLAMA_BIN="$(command -v llama-server || true)"
if [ -z "$LLAMA_BIN" ] || [ ! -x "$LLAMA_BIN" ]; then
  echo "[serve] 找不到 llama-server。装好 llama.cpp 后设置 LLAMA_DIR，或把 llama-server 放进 PATH。" >&2
  echo "        例：LLAMA_DIR=\$HOME/llama.cpp ./serve.sh" >&2
  exit 1
fi
export LD_LIBRARY_PATH="$LLAMA_DIR:$LLAMA_DIR/llama-b11723:$LLAMA_DIR/build/bin:$LLAMA_DIR/build-cuda/bin:${LD_LIBRARY_PATH:-}"
for d in "$LLAMA_DIR"/cudart-*; do
  [ -d "$d" ] && export LD_LIBRARY_PATH="$d:$LD_LIBRARY_PATH"
done

PROFILE="${PROFILE:-4b}"
case "$PROFILE" in
  4b)
    MODEL="${MODEL:-$HOME/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf}"
    CTX=4096; NP=1; BATCH=512; UB=128; KV_DEFAULT=q8_0 ;;
  small)
    MODEL="${MODEL:-$HOME/cyberdyne-demo/模型/qwen2.5-1.5b-instruct-q4_k_m.gguf}"
    CTX=8192; NP=1; BATCH=2048; UB=512; KV_DEFAULT=f16 ;;
  big)
    MODEL="${MODEL:-$HOME/models/DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf}"
    CTX=4096; NP=1; BATCH=2048; UB=512; KV_DEFAULT=f16 ;;
  *)
    echo "PROFILE 只能是 4b / small / big" >&2; exit 2 ;;
esac

PORT="${PORT:-8099}"
KV="${KV:-${KV_DEFAULT:-f16}}"   # 4b 默认 q8_0；small/big 默认 f16
FA="${FA:-auto}"         # 再炸就 FA=off（会慢一点，但稳）
THREADS="${THREADS:-8}"

if [ ! -f "$MODEL" ]; then
  echo "[serve] 模型不存在：$MODEL" >&2
  echo "        用 MODEL=/path/to/model.gguf ./serve.sh 指定" >&2
  exit 1
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  NGL="${NGL:-99}"
  free_mb=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
else
  NGL="${NGL:-0}"
  free_mb=0
fi

# 显存预检：模型文件大小 + 计算缓冲/KV 的粗估
need_mb=$(( $(stat -c %s "$MODEL") / 1024 / 1024 + 700 ))
echo "[serve] $PROFILE | $(basename "$MODEL") | ctx=$CTX np=$NP b=$BATCH ub=$UB kv=$KV ngl=$NGL | 需要≈${need_mb}M 可用 ${free_mb}M"
if [ "$NGL" -gt 0 ] && [ "${free_mb:-0}" -lt "$need_mb" ]; then
  echo "[serve] ⚠ 显存可能不够：把 NP 降到 1，或 CTX 减半，或 NGL=0 走 CPU" >&2
fi

exec "$LLAMA_BIN" \
  -m "$MODEL" --host 127.0.0.1 --port "$PORT" \
  -c "$CTX" -np "$NP" -b "$BATCH" -ub "$UB" \
  -ngl "$NGL" -fa "$FA" -t "$THREADS" \
  --cache-type-k "$KV" --cache-type-v "$KV" \
  --metrics --jinja "$@"
