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
    """GitHub Actions 実行中なら当該 run の URL を返します。

    Returns:
        当該 run の URL。GITHUB_* 環境変数が揃わないローカル実行時は空文字。
    """
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def post_to_slack(webhook_url: str, message: str) -> None:
    """Slack Webhook にメッセージを送信します。

    Args:
        webhook_url: 送信先の Slack Incoming Webhook URL。
        message: 送信本文。SLACK_TEXT_LIMIT を超える分は末尾を切り詰める。
    """
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


def require_webhook_url() -> str:
    """SLACK_WEBHOOK_URL を返します。未設定なら通知経路が無い異常としてエラー終了します。

    Returns:
        環境変数 SLACK_WEBHOOK_URL の値。
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print("Error: SLACK_WEBHOOK_URL is not set", file=sys.stderr)
        sys.exit(1)
    return webhook_url
