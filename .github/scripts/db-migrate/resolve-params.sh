#!/usr/bin/env bash
#
# db-migrate ワークフローの実行パラメータを解決し GITHUB_OUTPUT に書き出す。
#
# - project            : PROJECTS マップ (JSON) から TARGET_ENV に対応する Google Cloud プロジェクト ID
# - image_sha          : 常に ops repo の HEAD sha (= schemas.lock.yaml の HEAD)。
#                        repository_dispatch.client_payload.sha は送信元 repo の sha なので採用しない。
# - dry_run            : repository_dispatch 経由なら強制 false、手動 dispatch なら INPUT_DRY_RUN をそのまま。
# - bootstrap_baseline : 同上。適用済み union の記録が無い環境への初回適用は手動でのみ許可する。
#
# 必要な環境変数:
#   PROJECTS                  - '{"dev":"...","stg":"..."}' 形式の JSON
#   TARGET_ENV                - dev / stg
#   EVENT_NAME                - github.event_name (repository_dispatch / workflow_dispatch)
#   GIT_SHA                   - github.sha
#   INPUT_DRY_RUN             - workflow_dispatch の dry_run 入力 (repository_dispatch 時は空でよい)
#   INPUT_BOOTSTRAP_BASELINE  - workflow_dispatch の bootstrap_baseline 入力 (同上)
#   GITHUB_OUTPUT             - GitHub Actions が提供する出力ファイルパス
set -euo pipefail

: "${PROJECTS:?PROJECTS is required}"
: "${TARGET_ENV:?TARGET_ENV is required}"
: "${EVENT_NAME:?EVENT_NAME is required}"
: "${GIT_SHA:?GIT_SHA is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

project=$(echo "$PROJECTS" | jq -r --arg env "$TARGET_ENV" '.[$env] // empty')
if [ -z "$project" ]; then
  echo "resolve-params: unknown environment '$TARGET_ENV' (PROJECTS=$PROJECTS)" >&2
  exit 1
fi

case "$EVENT_NAME" in
  repository_dispatch)
    dry_run=false
    bootstrap_baseline=false
    ;;
  workflow_dispatch)
    : "${INPUT_DRY_RUN:?INPUT_DRY_RUN is required for workflow_dispatch}"
    : "${INPUT_BOOTSTRAP_BASELINE:?INPUT_BOOTSTRAP_BASELINE is required for workflow_dispatch}"
    dry_run="$INPUT_DRY_RUN"
    bootstrap_baseline="$INPUT_BOOTSTRAP_BASELINE"
    ;;
  *)
    echo "resolve-params: unsupported event '$EVENT_NAME'" >&2
    exit 1
    ;;
esac

{
  echo "project=$project"
  echo "image_sha=$GIT_SHA"
  echo "dry_run=$dry_run"
  echo "bootstrap_baseline=$bootstrap_baseline"
} >> "$GITHUB_OUTPUT"
