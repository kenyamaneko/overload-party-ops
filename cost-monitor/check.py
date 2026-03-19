#!/usr/bin/env python3
"""環境ごとのコスト発生リソースを検出し、Slack で通知する。"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

REGION = "asia-northeast1"
GKE_PROJECT = "keyandnotes-platform"
GKE_CLUSTER = "keyandnotes-shared"
CLOUDSQL_INSTANCE = "overload-party-db"
DEPLOYMENTS = ["gateway", "battle"]


def log(message: str, *, severity: str = "INFO") -> None:
    """Cloud Logging 構造化ログ出力。"""
    print(json.dumps({"message": message, "severity": severity}), flush=True)


def _is_not_found(stderr: str) -> bool:
    return "NotFound" in stderr or "not found" in stderr or "404" in stderr


def load_environments() -> dict[str, str]:
    """ENVIRONMENTS_JSON 環境変数から監視対象を読み込む。

    Terraform 側で定義した environments 変数を jsonencode して渡す想定。
    形式: {"dev": "overload-party-dev", "stg": "overload-party-stg"}
    """
    raw = os.environ.get("ENVIRONMENTS_JSON", "")
    if not raw:
        return {}
    return json.loads(raw)


def _run_cmd(
    cmd: list[str], *, label: str = "cmd", allow_not_found: bool = False,
) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and result.stderr.strip():
        if allow_not_found and _is_not_found(result.stderr):
            log(f"[{label}] {result.stderr.strip()}", severity="DEBUG")
        else:
            log(f"[{label}] {result.stderr.strip()}", severity="ERROR")
    return result.stdout.strip()


def gcloud(*args: str, allow_not_found: bool = False) -> str:
    return _run_cmd(["gcloud", *args, "--format=json"], label="gcloud", allow_not_found=allow_not_found)


def gcloud_value(*args: str, allow_not_found: bool = False) -> str:
    return _run_cmd(["gcloud", *args], label="gcloud", allow_not_found=allow_not_found)


def kubectl_json(*args: str, allow_not_found: bool = False) -> str:
    return _run_cmd(["kubectl", *args, "-o", "json"], label="kubectl", allow_not_found=allow_not_found)


def setup_gke_credentials() -> bool:
    result = subprocess.run(
        ["gcloud", "container", "clusters", "get-credentials", GKE_CLUSTER,
         "--region", REGION, "--project", GKE_PROJECT],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log(f"[gcloud] GKE credentials failed: {result.stderr.strip()}", severity="WARNING")
        return False
    return True


def check_cloudsql(project: str) -> list[str]:
    state = gcloud_value(
        "sql", "instances", "describe", CLOUDSQL_INSTANCE,
        "--project", project, "--format=value(state)",
    )
    if state == "RUNNABLE":
        return [f"Cloud SQL `{CLOUDSQL_INSTANCE}` が RUNNABLE ($0.19/hr)"]
    return []


def check_gke_deployments(env: str) -> list[str]:
    findings: list[str] = []
    for deploy in DEPLOYMENTS:
        raw = kubectl_json(
            "get", "deployment", deploy,
            "-n", env,
            f"--context=gke_{GKE_PROJECT}_{REGION}_{GKE_CLUSTER}",
        )
        if not raw:
            continue
        try:
            spec = json.loads(raw)
            replicas = spec.get("spec", {}).get("replicas", 0)
            if replicas > 0:
                findings.append(f"Deployment `{deploy}` が {replicas} レプリカ稼働中")
        except json.JSONDecodeError:
            log(f"Failed to parse deployment JSON for {deploy}", severity="WARNING")
    return findings


def check_ingress(env: str) -> list[str]:
    raw = kubectl_json(
        "get", "ingress", "overload-party",
        "-n", env,
        f"--context=gke_{GKE_PROJECT}_{REGION}_{GKE_CLUSTER}",
        allow_not_found=True,
    )
    if not raw:
        return []
    try:
        ing = json.loads(raw)
        ip_list = ing.get("status", {}).get("loadBalancer", {}).get("ingress", [])
        if ip_list:
            ip = ip_list[0].get("ip", "unknown")
            return [f"Ingress `overload-party` が稼働中 (IP: {ip}, ~$0.025/hr)"]
    except json.JSONDecodeError:
        log("Failed to parse ingress JSON", severity="WARNING")
    return []


def check_static_ips(project: str) -> list[str]:
    findings: list[str] = []
    raw = gcloud(
        "compute", "addresses", "list",
        "--project", project,
        "--filter", "status=RESERVED AND addressType=EXTERNAL",
    )
    try:
        addresses = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        log("Failed to parse static IPs JSON", severity="WARNING")
        addresses = []
    for addr in addresses:
        name = addr.get("name", "unknown")
        ip = addr.get("address", "unknown")
        findings.append(f"予約済み外部 IP `{name}` ({ip}, ~$3.65/mo)")
    return findings


def check_psc(project: str) -> list[str]:
    findings: list[str] = []
    raw = gcloud(
        "compute", "forwarding-rules", "list",
        "--project", project,
        "--filter", "target~serviceAttachments",
    )
    try:
        rules = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        log("Failed to parse PSC forwarding rules JSON", severity="WARNING")
        rules = []
    for rule in rules:
        name = rule.get("name", "unknown")
        findings.append(f"PSC forwarding rule `{name}` が稼働中")
    return findings


def namespace_exists(env: str) -> bool:
    result = subprocess.run(
        ["kubectl", "get", "namespace", env,
         f"--context=gke_{GKE_PROJECT}_{REGION}_{GKE_CLUSTER}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def check_environment(env: str, project: str, *, gke_available: bool = True) -> list[str]:
    findings: list[str] = []
    findings.extend(check_cloudsql(project))
    if gke_available and namespace_exists(env):
        findings.extend(check_gke_deployments(env))
        findings.extend(check_ingress(env))
    elif not gke_available:
        log("GKE credentials unavailable, skipping GKE checks.", severity="WARNING")
    else:
        log(f"Namespace '{env}' not found, skipping GKE checks.", severity="WARNING")
    findings.extend(check_static_ips(project))
    findings.extend(check_psc(project))
    return findings


def notify_slack(webhook_url: str, message: str) -> None:
    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        log(f"Slack notification failed: {e}", severity="ERROR")
        sys.exit(1)


def main() -> None:
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).strftime("%Y-%m-%d")

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        log("SLACK_WEBHOOK_URL is not set", severity="ERROR")
        sys.exit(1)

    environments = load_environments()
    if not environments:
        log("ENVIRONMENTS_JSON is not set or empty", severity="ERROR")
        sys.exit(1)

    gke_available = setup_gke_credentials()
    all_findings: dict[str, list[str]] = {}

    for env, project in environments.items():
        log(f"=== Checking {env} ({project}) ===")
        findings = check_environment(env, project, gke_available=gke_available)
        if findings:
            all_findings[env] = findings
            for f in findings:
                log(f"  - {f}")
        else:
            log("No cost-bearing resources detected.")

    if not all_findings:
        log("All clear.")
        return

    lines = [f":warning: *[コスト警告 {today}] 稼働中リソースあり*", ""]
    for env, findings in all_findings.items():
        lines.append(f"*{env}*")
        for f in findings:
            lines.append(f"  • {f}")
        lines.append("")

    message = "\n".join(lines)
    log(message, severity="WARNING")

    notify_slack(webhook_url, message)
    log("Slack notification sent.")


if __name__ == "__main__":
    main()
