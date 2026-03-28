#!/usr/bin/env bash

if [ -z "${RESULTS}" ]; then
  RESULTS="認証失敗のため未実行"
  HAS_ERROR=true
fi

if [ "${HAS_ERROR}" = "true" ]; then
  HEADER=":x: \`${ENV}\` の Nightly Shutdown に失敗しました <https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}|ログ>"
else
  HEADER=":crescent_moon: \`${ENV}\` の Nightly Shutdown が完了しました"
fi

MSG=$(printf "%s\n%s" "${HEADER}" "${RESULTS}")

if [ -n "${SLACK_WEBHOOK_URL}" ]; then
  curl -s -X POST "${SLACK_WEBHOOK_URL}" \
    -H 'Content-type: application/json' \
    --data "$(jq -n --arg text "${MSG}" '{text: $text}')"
fi

if [ "${HAS_ERROR}" = "true" ]; then
  exit 1
fi
