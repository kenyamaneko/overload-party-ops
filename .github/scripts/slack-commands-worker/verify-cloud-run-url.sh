#!/usr/bin/env bash
#
# wrangler.toml の CLOUD_RUN_URL を抽出し、/health に到達できることを確認する。
# Cloud Run プロジェクト移行などで URL が変わったまま放置された場合に
# デプロイ前に検知して止めるためのガード。
#
# 必要な環境変数:
#   WRANGLER_TOML - CLOUD_RUN_URL を含む wrangler.toml のパス
set -euo pipefail

: "${WRANGLER_TOML:?WRANGLER_TOML is required}"

url=$(awk -F'"' '/^CLOUD_RUN_URL[[:space:]]*=/ {print $2}' "${WRANGLER_TOML}")
if [ -z "${url}" ]; then
  echo "CLOUD_RUN_URL not found in ${WRANGLER_TOML}" >&2
  exit 1
fi

echo "Probing ${url}/health"
curl -fsS --max-time 10 "${url}/health"
