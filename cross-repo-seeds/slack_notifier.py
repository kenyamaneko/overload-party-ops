"""cross-repo seed 検証ジョブ共通の Slack 通知ユーティリティ。

検証内容に依存しない Webhook 送信と GitHub Actions run URL 組み立てを提供し、
card_pack 以外の cross-repo 検証を追加した際も同じ通知経路を再利用できるようにする。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

SLACK_TEXT_LIMIT = 40000


def build_actions_run_url() -> str:
    """GitHub Actions 実行中なら当該 run の URL を返します。ローカル実行時は空文字。"""
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def post_to_slack(webhook_url: str, message: str) -> None:
    """Slack Webhook にメッセージを送信します。"""
    if len(message) > SLACK_TEXT_LIMIT:
        message = message[:SLACK_TEXT_LIMIT] + "\n…(truncated)"
    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"Slack notification failed: {e}", file=sys.stderr)
        sys.exit(1)


def notify_if_configured(message: str) -> None:
    """SLACK_WEBHOOK_URL が設定されていれば message を Slack に通知します。"""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL is not set, skipping Slack notification", file=sys.stderr)
        return
    post_to_slack(webhook_url, message)
