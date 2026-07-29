"""gcloud ラッパと、各コスト発生リソースの稼働確認を提供する。"""
import json
import re
import subprocess

CLOUDSQL_INSTANCE = "overload-party-db"

# "リソースが存在しない" エラーのみ抑制するパターン。
# 認証失敗 / quota / network 等はマッチさせず例外で落とす。
_NOT_FOUND_RE = re.compile(
    r"(?:\bNotFound\b"
    r"|\bNOT_FOUND\b"
    r"|\bnot\s+found\b"
    r"|\bcould\s+not\s+be\s+found\b"
    r"|\bdoes\s+not\s+exist\b"
    r"|\b404\b)",
    re.IGNORECASE,
)


class CommandError(Exception):
    pass


def _is_not_found(stderr: str) -> bool:
    """stderr がリソース未存在を示すか判定します。"""
    return bool(_NOT_FOUND_RE.search(stderr))


def format_cmd_failure(stderr: str, stdout: str, returncode: int) -> str:
    """外部コマンド失敗時に stderr/stdout を両方拾った詳細メッセージを返す。

    CLI によっては stderr が空で stdout にしかエラーを吐かないケースがあり
    (Claude CLI 等)、片方だけを見ると silent failure の原因になる。
    subprocess を直接扱う箇所は必ずこのヘルパーを通すこと。
    """
    stderr_s = (stderr or "").strip()
    stdout_s = (stdout or "").strip()
    if stderr_s and stdout_s:
        return f"stderr: {stderr_s}\nstdout: {stdout_s}"
    if stderr_s:
        return stderr_s
    if stdout_s:
        return f"stdout: {stdout_s}"
    return f"exit code {returncode} (stderr/stdout ともに空)"


def _run_cmd(
    cmd: list[str], *, label: str = "cmd", allow_not_found: bool = False,
) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # `allow_not_found` はリソース未作成のケースのみ許可する。
        # 認証失敗等は not-found パターンにマッチしないため例外で落とす。
        if allow_not_found and stderr and _is_not_found(stderr):
            print(f"[{label}] {stderr}")
            return ""
        # 詳細は print で Actions ログに流し、Slack 向けの例外メッセージは短く保つ
        detail = format_cmd_failure(result.stderr, result.stdout, result.returncode)
        print(f"[{label}] {detail}")
        raise CommandError(f"{label} 実行失敗 (exit {result.returncode})")
    return result.stdout.strip()


def run_gcloud(*args: str, allow_not_found: bool = False) -> str:
    """gcloud コマンドを JSON 出力で実行します。"""
    return _run_cmd(["gcloud", *args, "--format=json"], label="gcloud", allow_not_found=allow_not_found)


def run_gcloud_value(*args: str, allow_not_found: bool = False) -> str:
    """gcloud コマンドをテキスト出力で実行します。"""
    return _run_cmd(["gcloud", *args], label="gcloud", allow_not_found=allow_not_found)


def check_cloudsql(project: str) -> tuple[list[str], list[str]]:
    """Cloud SQL インスタンスの稼働状態を確認します。"""
    try:
        state = run_gcloud_value(
            "sql", "instances", "describe", CLOUDSQL_INSTANCE,
            "--project", project, "--format=value(state)",
            allow_not_found=True,
        )
    except CommandError as e:
        return [], [f"Cloud SQL チェック失敗: {e}"]
    if not state:
        return [], []
    if state == "RUNNABLE":
        return [f"Cloud SQL `{CLOUDSQL_INSTANCE}` が RUNNABLE ($0.19/hr)"], []
    return [], []


def check_static_ips(project: str) -> tuple[list[str], list[str]]:
    """予約済み外部 IP アドレスを確認します。"""
    try:
        raw = run_gcloud(
            "compute", "addresses", "list",
            "--project", project,
            "--filter", "status=RESERVED AND addressType=EXTERNAL",
        )
    except CommandError as e:
        return [], [f"外部 IP チェック失敗: {e}"]
    try:
        addresses = json.loads(raw) if raw else []
    except json.JSONDecodeError as e:
        return [], [f"外部 IP JSON パース失敗: {e}"]
    costs: list[str] = []
    errors: list[str] = []
    for addr in addresses:
        name = addr.get("name")
        ip = addr.get("address")
        # name/address は GCP API 仕様上必ず返るフィールド。欠落は API 仕様変更
        # かフィルタ条件のミスマッチを示すので silent に "unknown" 表示せず errors に流す
        if name is None or ip is None:
            errors.append(f"外部 IP のレスポンスに name/address がありません: {addr}")
            continue
        costs.append(f"予約済み外部 IP `{name}` ({ip}, ~$3.65/mo)")
    return costs, errors


def check_psc(project: str) -> tuple[list[str], list[str]]:
    """PSC forwarding rule の稼働状態を確認します。"""
    try:
        raw = run_gcloud(
            "compute", "forwarding-rules", "list",
            "--project", project,
            "--filter", "target~serviceAttachments",
        )
    except CommandError as e:
        return [], [f"PSC チェック失敗: {e}"]
    try:
        rules = json.loads(raw) if raw else []
    except json.JSONDecodeError as e:
        return [], [f"PSC JSON パース失敗: {e}"]
    costs: list[str] = []
    errors: list[str] = []
    for rule in rules:
        name = rule.get("name")
        if name is None:
            errors.append(f"PSC forwarding rule のレスポンスに name がありません: {rule}")
            continue
        costs.append(f"PSC forwarding rule `{name}` が稼働中")
    return costs, errors


def check_environment(project: str) -> tuple[list[str], list[str]]:
    """指定プロジェクトのコスト発生リソースを一括チェックします。"""
    costs: list[str] = []
    errors: list[str] = []

    def _collect(result: tuple[list[str], list[str]]) -> None:
        costs.extend(result[0])
        errors.extend(result[1])

    _collect(check_cloudsql(project))
    _collect(check_static_ips(project))
    _collect(check_psc(project))
    return costs, errors
