#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

GITHUB_ORG = "kenyamaneko"
MAX_DIFF_CHARS = 150000

REPOS_YAML = Path(__file__).parent / "repos.yaml"


def load_repos() -> list[dict]:
    """リポジトリ設定を読み込む。各要素は {"name": str, "branch": str | None}。"""
    raw = os.environ.get("REPOS_JSON", "")
    if raw:
        return json.loads(raw)
    with open(REPOS_YAML) as f:
        return yaml.safe_load(f)

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


class GhError(Exception):
    pass


def gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        msg = f"gh {args[0]} failed: {result.stderr.strip()}"
        print(f"  Error: {msg}")
        raise GhError(msg)
    return result.stdout.strip()


def ensure_label(repo: str, label: str) -> None:
    try:
        existing = gh("label", "list", "--repo", f"{GITHUB_ORG}/{repo}", "--search", label)
    except GhError as e:
        print(f"  Warning: failed to list labels for {repo}: {e}")
        existing = ""
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
    try:
        output = gh(
            "issue", "list",
            "--repo", f"{GITHUB_ORG}/{repo}",
            "--search", f'in:title "{search_key}"',
            "--state", "open",
            "--json", "number",
            "--jq", "length",
        )
    except GhError:
        return False
    return output.isdigit() and int(output) > 0


def get_diff(repo: str, branch: str, since: str) -> str | None:
    commits_json = gh(
        "api", f"repos/{GITHUB_ORG}/{repo}/commits?sha={branch}&since={since}",
        "-q", "length",
    )
    commit_count = int(commits_json) if commits_json.isdigit() else 0
    if commit_count == 0:
        return None

    raw = gh(
        "api", f"repos/{GITHUB_ORG}/{repo}/compare/{branch}~{commit_count}...{branch}",
        "--jq", '.files[] | select(.patch != null) | "=== \\(.filename) ===\\n\\(.patch)"',
    )
    return raw if raw else None


def run_claude(prompt: str) -> str | None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(prompt)
        f.flush()
        try:
            with open(f.name) as stdin_file:
                result = subprocess.run(
                    ["claude", "-p", "--allowedTools", "Read,Grep,Glob"],
                    stdin=stdin_file,
                    capture_output=True, text=True,
                )
        finally:
            os.unlink(f.name)

    if result.returncode != 0:
        print(f"  Error: claude exited with code {result.returncode}: {result.stderr.strip()}")
        return None

    body = result.stdout.strip()
    return body if body else None


def truncate_diff(diff: str, limit: int) -> tuple[str, list[str]]:
    """差分をファイル単位で切り捨てる。

    Returns:
        (収まった差分テキスト, 切り捨てられたファイル名リスト)
    """
    if len(diff) <= limit:
        return diff, []

    file_sections = re.split(r"(?=^=== .+ ===$)", diff, flags=re.MULTILINE)
    kept: list[str] = []
    omitted: list[str] = []
    total = 0

    for section in file_sections:
        if not section:
            continue
        if total + len(section) > limit and kept:
            m = re.match(r"^=== (.+) ===$", section, re.MULTILINE)
            omitted.append(m.group(1) if m else "(unknown)")
        else:
            kept.append(section)
            total += len(section)

    return "".join(kept), omitted


def review_diff(repo: str, branch: str, yesterday: str) -> str | None:
    since = f"{yesterday}T00:00:00Z"
    diff = get_diff(repo, branch, since)
    if diff is None:
        print(f"  No changes since {yesterday}, skipping.")
        return None

    diff, omitted = truncate_diff(diff, MAX_DIFF_CHARS)

    note = ""
    if omitted:
        files = ", ".join(omitted)
        note = f"（注：差分が大きいため以下のファイルは省略されています: {files}）"

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


def notify_slack(title: str, body: str) -> None:
    # GitHub Actions secrets 経由で注入
    # 通知失敗はジョブ全体を止めるほどではないため Warning のみ出力して継続する
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return

    text = f"*{title}*\n{body}"
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

    repos = load_repos()

    for entry in repos:
        repo = entry["name"]
        branch = entry.get("branch")
        print(f"=== {repo} ===")

        if not branch:
            msg = f"`{repo}`: branch が未設定のためスキップしました"
            print(f"  {msg}")
            notify_slack("Nightly Review 設定エラー", msg)
            has_error = True
            continue

        title = f"[自動レビュー {today}] 差分 {repo}"

        if skip_if_exists and issue_exists(repo, f"[自動レビュー {today}] 差分"):
            print("  Issue already exists, skipping.")
            continue

        ensure_label(repo, label)

        try:
            body = review_diff(repo, branch, yesterday)
        except GhError:
            has_error = True
            continue

        if body is None:
            continue

        if is_no_issues(body):
            print("  No issues found, skipping issue creation.")
            continue

        issue_url = create_issue(repo, title, label, body)
        if issue_url is None:
            has_error = True
        else:
            notify_slack(title, f"レビューコメントがあります\n{issue_url}")

    print("=== Nightly review complete ===")
    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
