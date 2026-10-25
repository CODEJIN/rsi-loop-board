#!/usr/bin/env bash
# 사용: run_bench.sh <base_url> <rounds> <cpu> <out.json> [snapshot.json]
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out
SNAP=${5:+--snapshot /w/${5}}
exec docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/w:Z -w /w \
  mcr.microsoft.com/playwright:v1.47.0-jammy node tonight.mjs --base "$1" --rounds "$2" --cpu "$3" --out "/w/$4" $SNAP
