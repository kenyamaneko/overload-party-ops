"""gh CLI ラッパと GitHub API 経由の diff / ファイル全文 / Issue 操作。"""
import base64
import json
import subprocess

GITHUB_ORG = "kenyamaneko"


class GhError(Exception):
    """gh CLI の実行が失敗したことを示します。"""


class IssueCreateError(Exception):
    """GitHub Issue の作成が失敗したことを示します。"""


class FileContentFetchError(Exception):
    """変更ファイル全文の取得中に想定外のエラーが発生したことを示します。"""


def _format_cmd_failure(stderr: str, stdout: str) -> str:
    """外部コマンド失敗時の詳細メッセージを整形します。CLI によっては stdout にしかエラーを吐かないため両方拾う。"""
    stderr_s = (stderr or "").strip()
    stdout_s = (stdout or "").strip()
    if stderr_s and stdout_s:
        return f"stderr: {stderr_s}\nstdout: {stdout_s}"
    if stderr_s:
        return f"stderr: {stderr_s}"
    if stdout_s:
        return f"stdout: {stdout_s}"
    return "(stderr/stdout ともに空)"


def gh(*args: str) -> str:
    """gh CLI コマンドを実行します。"""
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
    """GitHub リポジトリにラベルが存在しなければ作成します。"""
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
    """同名の Issue が既に存在するか確認します。"""
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


def get_diff(repo: str, branch: str, since: str) -> tuple[str | None, list[dict]]:
    """指定日時以降の差分テキストと変更ファイル情報リストを返します。

    ファイル情報は {"filename": str, "status": str} の dict。
    status は GitHub Compare API の値（added/modified/removed/renamed/copied/changed）。
    """
    commits_json = gh(
        "api", f"repos/{GITHUB_ORG}/{repo}/commits?sha={branch}&since={since}",
        "-q", "length",
    )
    if not commits_json.isdigit():
        # gh -q length は必ず整数を返すはず。非数字なら gh CLI 仕様変更や
        # API レスポンスフォーマット変化のサインなので silent に 0 扱いせず投げる
        raise GhError(f"commit count が整数ではありません: {commits_json!r}")
    commit_count = int(commits_json)
    if commit_count == 0:
        return None, []

    compare_endpoint = f"repos/{GITHUB_ORG}/{repo}/compare/{branch}~{commit_count}...{branch}"

    # ファイル名・status・差分を 1 回の API コールで取得
    combined = gh(
        "api", compare_endpoint,
        "--jq", '.files[] | select(.patch != null) | {filename, status, patch}',
    )

    if not combined:
        return None, []

    files: list[dict] = []
    diff_parts: list[str] = []
    for line in combined.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            # silent skip するとレビュー対象から該当ファイルが無言で消えるため、
            # gh API のフォーマット変更等に気付けるようエラーで止める
            raise GhError(f"compare API 行の JSON パース失敗: {e}: {line!r}") from e
        try:
            files.append({"filename": obj["filename"], "status": obj["status"]})
            diff_parts.append(f"=== {obj['filename']} ===\n{obj['patch']}")
        except KeyError as e:
            raise GhError(f"compare API レスポンスに必須フィールドがありません: {e} in {obj!r}") from e

    raw = "\n".join(diff_parts)
    return (raw if raw else None), files


def get_file_contents(
    repo: str, branch: str, files: list[dict], limit: int,
) -> str:
    """変更ファイルの全文を GitHub API 経由で取得し、制限内で結合して返します。

    status=removed のファイルは branch HEAD に存在しないため取得対象から除外する。
    それ以外のファイルで取得または decode に失敗した場合は握りつぶさず例外を投げ、
    呼び出し側で Slack 通知に回す。
    """
    sections: list[str] = []
    total = 0

    for f in files:
        filename = f["filename"]
        if f["status"] == "removed":
            continue

        try:
            content = gh(
                "api", f"repos/{GITHUB_ORG}/{repo}/contents/{filename}?ref={branch}",
                "--jq", ".content",
            )
        except GhError as e:
            raise FileContentFetchError(f"{filename}: GitHub API 失敗 ({e})") from e

        try:
            decoded = base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception as e:
            raise FileContentFetchError(f"{filename}: base64 decode 失敗 ({e})") from e

        section = f"=== {filename} (full) ===\n{decoded}"
        if total + len(section) > limit and sections:
            break
        sections.append(section)
        total += len(section)

    return "\n".join(sections)


def create_issue(repo: str, title: str, label: str, body: str) -> str:
    """GitHub Issue を作成し、URL を返します。"""
    result = subprocess.run(
        ["gh", "issue", "create",
         "--repo", f"{GITHUB_ORG}/{repo}",
         "--title", title,
         "--label", label,
         "--body", body],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise IssueCreateError(_format_cmd_failure(result.stderr, result.stdout))
    issue_url = result.stdout.strip()
    print(f"  Created: {issue_url}")
    return issue_url
