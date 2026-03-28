#!/usr/bin/env bash
if [ -n "${SLACK_WEBHOOK_URL}" ]; then
  MSG=":x: Nightly Review が失敗しました <https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}|ログ>"
  curl -s -X POST "${SLACK_WEBHOOK_URL}" \
    -H 'Content-type: application/json' \
    --data "$(jq -n --arg text "${MSG}" '{text: $text}')"
fi
