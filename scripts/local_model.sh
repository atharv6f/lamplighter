#!/bin/sh
# Starts the server of one local model entry on 127.0.0.1, in the foreground,
# after checking the weights against the entry's hash.
#
#   scripts/local_model.sh SETTINGS NAME WEIGHTS
#
# The server gets no variable of your shell but the ones named here, and
# downloads and usage reports are off, so it reads only the weights given.
set -eu

[ $# -eq 3 ] || { echo "usage: $0 SETTINGS NAME WEIGHTS" >&2; exit 2; }
settings=$1 name=$2 weights=$3
repo="$(cd "$(dirname "$0")/.." && pwd)"

field() {
  uv run --project "$repo" lamplighter models field --settings "$settings" "$name" "$1"
}

want=$(field weights_sha256)
have=$(uv run --project "$repo" lamplighter models weights-hash "$weights")
if [ "$want" != "$have" ]; then
  echo "the weights do not match the settings for $name" >&2
  exit 1
fi

server=$(field server | cut -d ' ' -f 1)
dir=$(field server_dir | sed "s|^~|$HOME|")
port=$(field base_url | sed 's|.*:||; s|/$||')
model=$(field model)
window=$(field window)
slots=$(field concurrent_requests)

# Each model's advised sampling with thinking on, and the KV cache one
# token takes, by the model named in the weights. The KV sizes are from
# each model's config: its layers, KV heads and head size.
# TODO: check the sampling against each model card on the day the weights
# are downloaded, and put the card's address beside it. Until then these
# are the Qwen3 cards' values for thinking mode.
case $(basename "$weights") in
  *Qwen3.6-35B-A3B*) temp=0.6 top_p=0.95 top_k=20 min_p=0 kv_bytes=20480 ;;
  *Qwen3.8-27B*) temp=0.6 top_p=0.95 top_k=20 min_p=0 kv_bytes=65536 ;;
  *)
    echo "no sampling is known for the weights $(basename "$weights")" >&2
    exit 2
    ;;
esac

# The share of the GPU's working set vllm-metal may take for its weights
# and KV pool together. vllm-metal takes 0.92 unless told, and keeps it.
# TODO: set it from this Mac's working-set limit, as the largest step of
# 0.05 that keeps the weights and pool under the bake-off's memory limit.
vllm_fraction=""

case $server in
  llama.cpp)
    bin="$dir/bin"
    exec env -i HOME="$HOME" PATH="$bin:/usr/bin:/bin" \
      HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
      "$bin/llama-server" --host 127.0.0.1 --port "$port" -m "$weights" \
      --alias "$model" --jinja -np "$slots" -c "$((window * slots))" --no-webui \
      --temp "$temp" --top-p "$top_p" --top-k "$top_k" --min-p "$min_p"
    ;;
  vllm-metal)
    if [ -z "$vllm_fraction" ]; then
      echo "set vllm_fraction in $0 from this Mac's working-set limit first" >&2
      exit 2
    fi
    bin="$dir/.venv/bin"
    sampling="{\"temperature\": $temp, \"top_p\": $top_p, \"top_k\": $top_k, \"min_p\": $min_p}"
    exec env -i HOME="$HOME" PATH="$bin:/usr/bin:/bin" \
      HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
      VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 \
      "$bin/vllm" serve "$weights" --host 127.0.0.1 --port "$port" \
      --served-model-name "$model" --max-model-len "$window" \
      --max-num-seqs "$slots" --gpu-memory-utilization "$vllm_fraction" \
      --enable-auto-tool-choice --tool-call-parser qwen3_coder \
      --reasoning-parser qwen3 --enable-prompt-tokens-details \
      --override-generation-config "$sampling"
    ;;
  mlx-lm)
    bin="$dir/.venv/bin"
    exec env -i HOME="$HOME" PATH="$bin:/usr/bin:/bin" \
      HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
      "$bin/mlx_lm.server" --host 127.0.0.1 --port "$port" --model "$weights" \
      --decode-concurrency "$slots" --prompt-concurrency "$slots" \
      --prompt-cache-bytes "$((kv_bytes * window * slots))" \
      --allowed-origins "http://127.0.0.1:$port" \
      --temp "$temp" --top-p "$top_p" --top-k "$top_k" --min-p "$min_p"
    ;;
  *)
    echo "unknown server: $server" >&2
    exit 2
    ;;
esac
