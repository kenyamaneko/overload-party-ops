#!/usr/bin/env python3
"""環境ごとのコスト発生リソースを検出し、Slack で通知する。"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REGION = "asia-northeast1"
GKE_PROJECT = "keyandnotes-platform"
GKE_CLUSTER = "keyandnotes-shared"
CLOUDSQL_INSTANCE = "overload-party-db"
DEPLOYMENTS = ["gateway", "battle", "account", "card", "matchmaking", "shop", "scenario"]
ENVIRONMENTS_YAML = Path(__file__).parent / "environments.yaml"

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


def _is_not_found(stderr: str) -> bool:
    """stderr がリソース未存在を示すか判定します。"""
    return bool(_NOT_FOUND_RE.search(stderr))


def load_environments() -> dict[str, str]:
    """監視対象の環境一覧を読み込みます。"""
    raw = os.environ.get("ENVIRONMENTS_JSON", "")
    if raw:
        return json.loads(raw)
    with open(ENVIRONMENTS_YAML) as f:
        return yaml.safe_load(f)


class CommandError(Exception):
    pass


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


def gcloud(*args: str, allow_not_found: bool = False) -> str:
    """gcloud コマンドを JSON 出力で実行します。"""
    return _run_cmd(["gcloud", *args, "--format=json"], label="gcloud", allow_not_found=allow_not_found)


def gcloud_value(*args: str, allow_not_found: bool = False) -> str:
    """gcloud コマンドをテキスト出力で実行します。"""
    return _run_cmd(["gcloud", *args], label="gcloud", allow_not_found=allow_not_found)


def kubectl_json(*args: str, allow_not_found: bool = False) -> str:
    """kubectl コマンドを JSON 出力で実行します。"""
    return _run_cmd(["kubectl", *args, "-o", "json"], label="kubectl", allow_not_found=allow_not_found)


def setup_gke_credentials() -> tuple[bool, str | None]:
    """GKE クラスタの認証情報を取得します。

    成功時: (True, None) / 失敗時: (False, エラー詳細)。
    失敗詳細は呼び出し側で Slack に必ず流すこと。print だけだと Actions
    ログにしか出ず、ユーザーは気付けない。
    """
    result = subprocess.run(
        ["gcloud", "container", "clusters", "get-credentials", GKE_CLUSTER,
         "--region", REGION, "--project", GKE_PROJECT],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = format_cmd_failure(result.stderr, result.stdout, result.returncode)
        print(f"[gcloud] GKE credentials failed: {detail}")
        return False, "GKE 認証失敗 (詳細はログ)"
    return True, None


def check_cloudsql(project: str) -> tuple[list[str], list[str]]:
    """Cloud SQL インスタンスの稼働状態を確認します。"""
    try:
        state = gcloud_value(
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


def check_gke_deployments(env: str) -> tuple[list[str], list[str]]:
    """GKE Deployment のレプリカ数を確認します。"""
    costs: list[str] = []
    errors: list[str] = []
    for deploy in DEPLOYMENTS:
        try:
            raw = kubectl_json(
                "get", "deployment", deploy,
                "-n", env,
                f"--context=gke_{GKE_PROJECT}_{REGION}_{GKE_CLUSTER}",
            )
        except CommandError as e:
            errors.append(f"Deployment `{deploy}` チェック失敗: {e}")
            continue
        if not raw:
            continue
        try:
            spec = json.loads(raw)
            replicas = spec.get("spec", {}).get("replicas", 0)
            if replicas > 0:
                costs.append(f"Deployment `{deploy}` が {replicas} レプリカ稼働中")
        except json.JSONDecodeError as e:
            errors.append(f"Deployment `{deploy}` JSON パース失敗: {e}")
    return costs, errors


def check_ingress(env: str) -> tuple[list[str], list[str]]:
    """Ingress リソースの稼働状態を確認します。"""
    try:
        raw = kubectl_json(
            "get", "ingress", "overload-party",
            "-n", env,
            f"--context=gke_{GKE_PROJECT}_{REGION}_{GKE_CLUSTER}",
            allow_not_found=True,
        )
    except CommandError as e:
        return [], [f"Ingress チェック失敗: {e}"]
    if not raw:
        return [], []
    try:
        ing = json.loads(raw)
        ip_list = ing.get("status", {}).get("loadBalancer", {}).get("ingress", [])
        if ip_list:
            ip = ip_list[0].get("ip", "unknown")
            return [f"Ingress `overload-party` が稼働中 (IP: {ip}, ~$0.025/hr)"], []
    except json.JSONDecodeError as e:
        return [], [f"Ingress JSON パース失敗: {e}"]
    return [], []


def check_static_ips(project: str) -> tuple[list[str], list[str]]:
    """予約済み外部 IP アドレスを確認します。"""
    try:
        raw = gcloud(
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
    for addr in addresses:
        name = addr.get("name", "unknown")
        ip = addr.get("address", "unknown")
        costs.append(f"予約済み外部 IP `{name}` ({ip}, ~$3.65/mo)")
    return costs, []


def check_psc(project: str) -> tuple[list[str], list[str]]:
    """PSC forwarding rule の稼働状態を確認します。"""
    try:
        raw = gcloud(
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
    for rule in rules:
        name = rule.get("name", "unknown")
        costs.append(f"PSC forwarding rule `{name}` が稼働中")
    return costs, []


def namespace_exists(env: str) -> tuple[bool, str | None]:
    """Kubernetes namespace が存在するか確認します。

    戻り値: (存在するか, エラー詳細)。NotFound は (False, None)、
    それ以外 (認証失敗/RBAC 等) は (False, エラー詳細) を返し、呼び出し側で
    Slack に流すこと。
    """
    result = subprocess.run(
        ["kubectl", "get", "namespace", env,
         f"--context=gke_{GKE_PROJECT}_{REGION}_{GKE_CLUSTER}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, None
    combined = f"{result.stderr}\n{result.stdout}"
    if _is_not_found(combined):
        return False, None
    detail = format_cmd_failure(result.stderr, result.stdout, result.returncode)
    print(f"[kubectl] namespace check failed: {detail}")
    return False, f"Namespace `{env}` 確認失敗 (詳細はログ)"


def check_environment(
    env: str, project: str, *, gke_available: bool = True,
) -> tuple[list[str], list[str]]:
    """指定環境のコスト発生リソースを一括チェックします。"""
    costs: list[str] = []
    errors: list[str] = []

    def _collect(result: tuple[list[str], list[str]]) -> None:
        costs.extend(result[0])
        errors.extend(result[1])

    _collect(check_cloudsql(project))
    if gke_available:
        ns_ok, ns_err = namespace_exists(env)
        if ns_err:
            errors.append(ns_err)
        if ns_ok:
            _collect(check_gke_deployments(env))
            _collect(check_ingress(env))
        elif not ns_err:
            print(f"Namespace '{env}' not found, skipping GKE checks.")
    else:
        print("GKE credentials unavailable, skipping GKE checks.")
    _collect(check_static_ips(project))
    _collect(check_psc(project))
    return costs, errors


def _actions_run_url() -> str:
    """GitHub Actions 実行中なら当該 run の URL を返します。ローカル実行時は空文字。"""
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


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

    gke_available, gke_auth_err = setup_gke_credentials()
    all_costs: dict[str, list[str]] = {}
    all_errors: dict[str, list[str]] = {}
    # GKE 認証失敗は全環境共通のエラーとして Slack に必ず載せる
    if gke_auth_err:
        all_errors["(shared)"] = [gke_auth_err]

    for env, project in environments.items():
        print(f"=== Checking {env} ({project}) ===")
        costs, errors = check_environment(env, project, gke_available=gke_available)
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
        run_url = _actions_run_url()
        header = f":x: *[コスト確認 {today}] チェックエラー*"
        if run_url:
            header += f" <{run_url}|ログ>"
        lines.append(header)
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
