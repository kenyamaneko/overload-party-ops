#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone

GITHUB_ORG = "kenyamaneko"
MAX_DIFF_CHARS = 50000

REPOS = [
    "overload-party-common",
    "overload-party-client",
    "overload-party-battle",
    "overload-party-gateway",
    "overload-party-infra",
    "overload-party-k8s",
    "overload-party-newsfeed",
    "overload-party-analytics",
    "overload-party-ops",
]

REVIEW_CRITERIA = (
    "以下の観点でレビューしてください。\n"
    "- 拡張性と保守性が高い設計であること\n"
    "- 同じような処理を複数箇所に書いていないか\n"
    "- バグのリスクがないか\n"
    "- 使用していないコードがないか\n"
    "- 場当たり的なワークアラウンドで設計を汚していないか\n"
    "- 未実装のTODOがないか\n"
    "- ドキュメントとコードの乖離がないか\n"
    "- エラーハンドリングが適切か（エラーを握りつぶしていないか）\n"
    "- テストコードは仕様に沿っているか\n"
    "- テストコードのデータパターンが十分か\n"
    "- テストを通すために設計を汚していないか\n"
    "- クラス、関数などの責務が明確に分離されているか\n"
    "- リポジトリ間の責務が明確に分離されているか\n"
)

DIFF_PROMPT = (
    "以下は昨日からの差分です。\n"
    f"{REVIEW_CRITERIA}"
    "問題がなければ LGTM とだけ返してください。"
    "Markdown形式で出力してください。"
)


def gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def ensure_label(repo: str, label: str) -> None:
    existing = gh("label", "list", "--repo", f"{GITHUB_ORG}/{repo}", "--search", label)
    if label not in existing:
        result = subprocess.run(
            ["gh", "label", "create", label,
             "--repo", f"{GITHUB_ORG}/{repo}",
             "--color", "0e8a16",
             "--description", "Nightly auto-review"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  Warning: failed to create label '{label}': {result.stderr.strip()}")


def issue_exists(repo: str, search_key: str) -> bool:
    output = gh(
        "issue", "list",
        "--repo", f"{GITHUB_ORG}/{repo}",
        "--search", f'in:title "{search_key}"',
        "--state", "open",
        "--json", "number",
        "--jq", "length",
    )
    return output.isdigit() and int(output) > 0


# TODO: 現在は main ブランチのみ対象。ブランチ管理が整ったら
#       オープンな PR やフィーチャーブランチも差分レビュー対象にする。
def get_diff(repo: str, since: str) -> str | None:
    commits_json = gh(
        "api", f"repos/{GITHUB_ORG}/{repo}/commits?sha=main&since={since}",
        "-q", "length",
    )
    commit_count = int(commits_json) if commits_json.isdigit() else 0
    if commit_count == 0:
        return None

    raw = gh(
        "api", f"repos/{GITHUB_ORG}/{repo}/compare/main~{commit_count}...main",
        "--jq", '.files[] | select(.patch != null) | "=== \\(.filename) ===\\n\\(.patch)"',
    )
    return raw if raw else None


def run_claude(prompt: str) -> str | None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(prompt)
        f.flush()
        try:
            result = subprocess.run(
                ["claude", "-p", "--allowedTools", "Read,Grep,Glob"],
                stdin=open(f.name),
                capture_output=True, text=True,
            )
        finally:
            os.unlink(f.name)

    if result.returncode != 0:
        print(f"  Error: claude exited with code {result.returncode}: {result.stderr.strip()}")
        return None

    body = result.stdout.strip()
    return body if body else None


def review_diff(repo: str, yesterday: str) -> str | None:
    since = f"{yesterday}T00:00:00Z"
    diff = get_diff(repo, since)
    if diff is None:
        print(f"  No changes since {yesterday}, skipping.")
        return None

    truncated = len(diff) >= MAX_DIFF_CHARS
    diff = diff[:MAX_DIFF_CHARS]

    note = "（注：差分が大きいため一部のみ表示）" if truncated else ""
    prompt = f"{DIFF_PROMPT}{note}\n\n```diff\n{diff}\n```"
    return run_claude(prompt)


def create_issue(repo: str, title: str, label: str, body: str) -> str | None:
    """Create a GitHub Issue and return its URL, or None on failure."""
    result = subprocess.run(
        ["gh", "issue", "create",
         "--repo", f"{GITHUB_ORG}/{repo}",
         "--title", title,
         "--label", label,
         "--body", body],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  Error: failed to create issue: {result.stderr.strip()}")
        return None
    issue_url = result.stdout.strip()
    print(f"  Created: {issue_url}")
    return issue_url


def is_no_issues(body: str) -> bool:
    return body.strip() == "LGTM"


def notify_slack(title: str, issue_url: str) -> None:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return

    text = f"*{title}*\nレビューコメントがあります\n{issue_url}"
    payload = json.dumps({"text": text})

    req = urllib.request.Request(
        webhook_url,
        data=payload.encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status != 200:
                print(f"  Warning: Slack notification failed: {resp.status}")
    except Exception as e:
        print(f"  Warning: Slack notification failed: {e}")


def main() -> None:
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).strftime("%Y-%m-%d")
    yesterday = (datetime.now(jst) - timedelta(days=1)).strftime("%Y-%m-%d")

    skip_if_exists = os.environ.get("SKIP_IF_EXISTS", "true") == "true"
    label = "auto-review"

    has_error = False

    for repo in REPOS:
        print(f"=== {repo} ===")

        title = f"[自動レビュー {today}] 差分 {repo}"

        if skip_if_exists and issue_exists(repo, f"[自動レビュー {today}] 差分"):
            print("  Issue already exists, skipping.")
            continue

        ensure_label(repo, label)

        body = review_diff(repo, yesterday)

        if body is None:
            continue

        if is_no_issues(body):
            print("  No issues found, skipping issue creation.")
            continue

        issue_url = create_issue(repo, title, label, body)
        if issue_url is None:
            has_error = True
        else:
            notify_slack(title, issue_url)

    print("=== Nightly review complete ===")
    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
