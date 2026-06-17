#!/usr/bin/env python3
"""環境ごとのコスト発生リソースを検出し、Slack で通知する。"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from resources import check_environment, setup_gke_credentials

ENVIRONMENTS_YAML = Path(__file__).parent / "environments.yaml"


def load_environments() -> dict[str, str]:
    """監視対象の環境一覧を読み込みます。"""
    raw = os.environ.get("ENVIRONMENTS_JSON", "")
    if raw:
        return json.loads(raw)
    with open(ENVIRONMENTS_YAML) as f:
        return yaml.safe_load(f)


def build_actions_run_url() -> str:
    """GitHub Actions 実行中なら当該 run の URL を返します。ローカル実行時は空文字。"""
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def _build_error_header(today: str, run_url: str) -> str:
    """チェックエラー通知のヘッダを組み立てる。

    Slack に載る短いエラー文（"gcloud 実行失敗 (exit 1)" 等）だけでは原因追跡に
    不十分なため、必ず詳細ログへの動線を一緒に載せる。動線が取れない時も
    「動線なし」を silent に省略せず明示することで、ユーザーが調査を諦めず
    ログ取得方法を問い合わせる起点になるようにする。
    """
    header = f":x: *[コスト確認 {today}] チェックエラー*"
    if run_url:
        return f"{header} <{run_url}|ログ>"
    return f"{header} (ログ URL 取得不可: GitHub Actions 外の実行 — stdout を確認)"


def notify_slack(webhook_url: str, message: str) -> None:
    """Slack Webhook にメッセージを送信します。"""
    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"Slack notification failed: {e}")
        sys.exit(1)


def main() -> None:
    """全環境のコスト発生リソースを確認し、Slack で通知します。"""
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).strftime("%Y-%m-%d")

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL is not set")
        sys.exit(1)

    environments = load_environments()
    if not environments:
        print("No environments loaded")
        sys.exit(1)

    is_gke_available, gke_auth_err = setup_gke_credentials()
    all_costs: dict[str, list[str]] = {}
    all_errors: dict[str, list[str]] = {}
    # GKE 認証失敗は全環境共通のエラーとして Slack に必ず載せる
    if gke_auth_err:
        all_errors["(shared)"] = [gke_auth_err]

    for env, project in environments.items():
        print(f"=== Checking {env} ({project}) ===")
        costs, errors = check_environment(env, project, is_gke_available=is_gke_available)
        if costs:
            all_costs[env] = costs
        if errors:
            all_errors[env] = errors
        for c in costs:
            print(f"  - {c}")
        for e in errors:
            print(f"  - [ERROR] {e}")
        if not costs and not errors:
            print("No cost-bearing resources detected.")

    if not all_costs and not all_errors:
        message = f":white_check_mark: *[コスト確認 {today}] 稼働中リソースなし*"
        print(message)
        notify_slack(webhook_url, message)
        print("Slack notification sent.")
        return

    lines: list[str] = []
    if all_costs:
        lines.append(f":warning: *[コスト警告 {today}] 稼働中リソースあり*")
        lines.append("")
        for env, costs in all_costs.items():
            lines.append(f"*{env}*")
            for c in costs:
                lines.append(f"  • {c}")
            lines.append("")

    if all_errors:
        # 詳細は Actions ログに出ているため、ユーザーが辿れるよう URL を載せる
        lines.append(_build_error_header(today, build_actions_run_url()))
        lines.append("")
        for env, errors in all_errors.items():
            lines.append(f"*{env}*")
            for e in errors:
                lines.append(f"  • {e}")
            lines.append("")

    message = "\n".join(lines)
    print(message)

    notify_slack(webhook_url, message)
    print("Slack notification sent.")

    if all_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
