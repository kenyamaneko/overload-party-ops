#!/usr/bin/env python3
"""全リポジトリの前日差分を Claude でレビューし、GitHub Issue と Slack 通知を作成する。"""
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from gh_client import (
    FileContentFetchError,
    GhError,
    IssueCreateError,
    _format_cmd_failure,
    create_issue,
    ensure_label,
    get_diff,
    get_file_contents,
    issue_exists,
)

# 合計が MAX_PROMPT_CHARS を超えない値にすること（DIFF + FILE_CONTENT + ヘッダ < PROMPT）
MAX_DIFF_CHARS = 300000
MAX_FILE_CONTENT_CHARS = 700000
# Claude CLI は 1M context を扱えるため、出力分の余白を残して 1.1M に設定
MAX_PROMPT_CHARS = 1100000

REPOS_YAML = Path(__file__).parent / "repos.yaml"


def load_repos() -> list[dict]:
    """レビュー対象リポジトリの設定を読み込みます。"""
    raw = os.environ.get("REPOS_JSON", "")
    if raw:
        return json.loads(raw)
    with open(REPOS_YAML) as f:
        return yaml.safe_load(f)

REVIEW_CRITERIA = (
    "以下の観点でレビューしてください。\n"
    "- 設計通りに実装されているか（設計ドキュメント・既存設計と実装の整合性）\n"
    "- 拡張性と保守性が高い設計であること\n"
    "- 同じような処理を複数箇所に書いていないか\n"
    "- バグのリスクがないか\n"
    "- セキュリティリスクがないか\n"
    "- 使用していないコードがないか\n"
    "- 場当たり的なワークアラウンドで設計を汚していないか\n"
    "- 未実装のTODOがないか\n"
    "- ディレクトリ構成が整理されているか\n"
    "- ドキュメントとコードの乖離がないか\n"
    "- エラーハンドリングが適切か（エラーを握りつぶしていないか）\n"
    "- テストコードは仕様に沿っているか\n"
    "- テストコードのデータパターンが十分か\n"
    "- テストを通すために設計を汚していないか\n"
    "- 実装をなぞるだけのテストになっていないか（仕様ベースになっているか）\n"
    "- 実装をなぞるだけのコメントがないか（Docコメントは除く）\n"
    "- 実装意図がわかりにくい箇所に意図を表すコメントがあるか\n"
    "- ファイル・クラス・関数の責務が明確に分離されているか\n"
    "- リポジトリ間の責務が明確に分離されているか\n"
    "- 通信用の文字列（エンドポイント、イベント名、ヘッダ名、トピック名など）はリテラル直書きではなく共通パッケージの定数を使っているか\n"
)

DIFF_PROMPT = (
    "以下は昨日からの差分です。\n"
    f"{REVIEW_CRITERIA}"
    "問題がなければ LGTM とだけ返してください。"
    "Markdown形式で出力してください。"
)


class PromptTooLargeError(Exception):
    """差分が大きすぎてプロンプト上限に収まらないことを示します。"""


class ClaudeError(Exception):
    """Claude CLI の実行が失敗したことを示します。"""


def run_claude(prompt: str) -> str | None:
    """Claude CLI にプロンプトを渡してレビュー結果を取得します。"""
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
        # Claude CLI はエラーを stdout に吐くことがあるため両方載せる
        detail = _format_cmd_failure(result.stderr, result.stdout)
        raise ClaudeError(f"exit code {result.returncode}: {detail}")

    body = result.stdout.strip()
    return body if body else None


def truncate_diff(diff: str, limit: int) -> tuple[str, list[str]]:
    """差分をファイル単位で切り捨て、(収まったテキスト, 省略ファイル名リスト) を返します。"""
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
            # split が === xxx === の lookahead で分割しているため、
            # ここに来る非空セクションは必ず === xxx === で始まる
            m = re.match(r"^=== (.+) ===$", section, re.MULTILINE)
            omitted.append(m.group(1))
        else:
            kept.append(section)
            total += len(section)

    return "".join(kept), omitted


def review_diff(repo: str, branch: str, yesterday: str) -> str | None:
    """指定リポジトリの前日差分を Claude でレビューします。"""
    since = f"{yesterday}T00:00:00Z"
    diff, files = get_diff(repo, branch, since)
    if diff is None:
        print(f"  No changes since {yesterday}, skipping.")
        return None

    diff, omitted = truncate_diff(diff, MAX_DIFF_CHARS)

    note = ""
    if omitted:
        omitted_names = ", ".join(omitted)
        note = f"（注：差分が大きいため以下のファイルは省略されています: {omitted_names}）\n"

    file_context = ""
    if files:
        print(f"  Fetching full content for {len(files)} changed files...")
        file_context = get_file_contents(repo, branch, files, MAX_FILE_CONTENT_CHARS)
        if file_context:
            file_context = (
                "\n\n以下は変更されたファイルの全文です。\n\n"
                f"```\n{file_context}\n```"
            )

    prompt = f"{DIFF_PROMPT}{note}\n\n```diff\n{diff}\n```{file_context}"
    if len(prompt) > MAX_PROMPT_CHARS:
        raise PromptTooLargeError(
            f"プロンプト {len(prompt):,} chars "
            f"(diff={len(diff):,} chars, files={len(files)}) "
            f"が上限 {MAX_PROMPT_CHARS:,} を超過"
        )
    return run_claude(prompt)


def is_no_issues(body: str) -> bool:
    """レビュー結果が LGTM（指摘なし）かどうかを判定します。"""
    return body.strip() == "LGTM"


def _actions_run_url() -> str:
    """GitHub Actions 実行中なら当該 run の URL を返します。ローカル実行時は空文字。"""
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def _log_reference() -> str:
    """Slack 本文に埋め込む「ログ所在」のテキスト。

    Actions URL が取れる時はリンク、取れない時は「取得不可」理由を明示する。
    「詳細はログ」とだけ書いてユーザーが実際の所在を探せない状態を作らないため、
    silent に URL を省略せず、どちらのケースも明示的に表現する。
    """
    run_url = _actions_run_url()
    if run_url:
        return f"<{run_url}|Actions ログ>"
    return "ログ URL 取得不可 (GitHub Actions 外の実行 — stdout を確認)"


def notify_slack(title: str, body: str) -> None:
    """Slack Webhook にレビュー結果を通知します。"""
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


def review_repo(entry: dict, today: str, yesterday: str, skip_if_exists: bool, label: str) -> bool:
    """単一リポジトリをレビューします。エラー発生時は True、正常終了時は False を返します。"""
    repo = entry["name"]
    branch = entry.get("branch")
    print(f"=== {repo} ===")

    if not branch:
        msg = f"`{repo}`: branch が未設定のためスキップしました"
        print(f"  {msg}")
        notify_slack("Nightly Review 設定エラー", msg)
        return True

    title = f"[自動レビュー {today}] 差分 {repo}"

    if skip_if_exists and issue_exists(repo, f"[自動レビュー {today}] 差分"):
        print("  Issue already exists, skipping.")
        return False

    ensure_label(repo, label)

    try:
        body = review_diff(repo, branch, yesterday)
    except PromptTooLargeError as e:
        # 文字数計算情報は短いので Slack にも載せる
        print(f"  Error: {e}")
        notify_slack(
            f"Nightly Review スキップ: {repo}",
            f"変更が多すぎて自動レビューできません\n{e}",
        )
        return True
    except ClaudeError as e:
        # stderr/stdout 詳細は print で Actions ログに流す。Slack は短く
        print(f"  Error: {e}")
        notify_slack(f"Nightly Review エラー: {repo}", f"Claude CLI 失敗 (詳細: {_log_reference()})")
        return True
    except FileContentFetchError as e:
        # レビュー対象ファイルの一部が欠けるとレビュー品質が無言で劣化するため、
        # 取得失敗は握りつぶさずに Slack に必ず流して人間に判断を委ねる。
        print(f"  Error: {e}")
        notify_slack(
            f"Nightly Review エラー: {repo}",
            f"変更ファイルの全文取得に失敗しました\n{e}",
        )
        return True
    except GhError as e:
        print(f"  Error: {e}")
        notify_slack(f"Nightly Review エラー: {repo}", f"GitHub API 失敗 (詳細: {_log_reference()})")
        return True

    if body is None:
        return False

    if is_no_issues(body):
        print("  No issues found, skipping issue creation.")
        return False

    try:
        issue_url = create_issue(repo, title, label, body)
    except IssueCreateError as e:
        print(f"  Error: {e}")
        notify_slack(f"Nightly Review エラー: {repo}", f"Issue 作成失敗 (詳細: {_log_reference()})")
        return True

    notify_slack(title, f"レビューコメントがあります\n{issue_url}")
    return False


def main() -> None:
    """全リポジトリの前日差分をレビューし、Issue を作成します。"""
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).strftime("%Y-%m-%d")
    yesterday = (datetime.now(jst) - timedelta(days=1)).strftime("%Y-%m-%d")

    skip_if_exists = os.environ.get("SKIP_IF_EXISTS", "true") == "true"
    label = "auto-review"

    has_error = False

    try:
        repos = load_repos()
        for entry in repos:
            try:
                if review_repo(entry, today, yesterday, skip_if_exists, label):
                    has_error = True
            except Exception:
                # Traceback は print で Actions ログに流す。Slack は短く
                tb = traceback.format_exc()
                print(tb)
                repo = entry.get("name", "(unknown)")
                exc_line = tb.strip().splitlines()[-1] if tb.strip() else "(unknown)"
                notify_slack(
                    f"Nightly Review 想定外エラー: {repo}",
                    f"{exc_line}\n詳細: {_log_reference()}",
                )
                has_error = True
    except Exception:
        # リポジトリループ前（設定読み込み等）の想定外エラー
        tb = traceback.format_exc()
        print(tb)
        exc_line = tb.strip().splitlines()[-1] if tb.strip() else "(unknown)"
        notify_slack("Nightly Review 想定外エラー", f"{exc_line}\n詳細: {_log_reference()}")
        sys.exit(1)

    print("=== Nightly review complete ===")
    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
