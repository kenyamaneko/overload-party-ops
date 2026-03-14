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
GKE_CLUSTER = "overload-party"
CLOUDSQL_INSTANCE = "overload-party-db"
DEPLOYMENTS = ["gateway", "battle"]


def load_environments() -> dict[str, str]:
    """ENVIRONMENTS_JSON 環境変数から監視対象を読み込む。

    Terraform 側で定義した environments 変数を jsonencode して渡す想定。
    形式: {"dev": "overload-party-dev", "stg": "overload-party-stg"}
    """
    raw = os.environ.get("ENVIRONMENTS_JSON", "")
    if not raw:
        return {}
    return json.loads(raw)


def gcloud(*args: str) -> str:
    result = subprocess.run(
        ["gcloud", *args, "--format=json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and result.stderr.strip():
        print(f"  [gcloud error] {result.stderr.strip()}", file=sys.stderr)
    return result.stdout.strip()


def gcloud_value(*args: str) -> str:
    result = subprocess.run(
        ["gcloud", *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and result.stderr.strip():
        print(f"  [gcloud error] {result.stderr.strip()}", file=sys.stderr)
    return result.stdout.strip()


def kubectl_json(*args: str) -> str:
    result = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and result.stderr.strip():
        print(f"  [kubectl error] {result.stderr.strip()}", file=sys.stderr)
    return result.stdout.strip()


def setup_gke_credentials() -> None:
    subprocess.run(
        ["gcloud", "container", "clusters", "get-credentials", GKE_CLUSTER,
         "--region", REGION, "--project", GKE_PROJECT],
        capture_output=True, text=True,
    )


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
            pass
    return findings


def check_ingress(env: str) -> list[str]:
    raw = kubectl_json(
        "get", "ingress", "overload-party",
        "-n", env,
        f"--context=gke_{GKE_PROJECT}_{REGION}_{GKE_CLUSTER}",
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
        pass
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


def check_environment(env: str, project: str) -> list[str]:
    findings: list[str] = []
    findings.extend(check_cloudsql(project))
    if namespace_exists(env):
        findings.extend(check_gke_deployments(env))
        findings.extend(check_ingress(env))
    else:
        print(f"  Namespace '{env}' not found, skipping GKE checks.")
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
        print(f"Slack notification failed: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).strftime("%Y-%m-%d")

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print("Error: SLACK_WEBHOOK_URL is not set", file=sys.stderr)
        sys.exit(1)

    environments = load_environments()
    if not environments:
        print("Error: ENVIRONMENTS_JSON is not set or empty", file=sys.stderr)
        sys.exit(1)

    setup_gke_credentials()
    all_findings: dict[str, list[str]] = {}

    for env, project in environments.items():
        print(f"=== Checking {env} ({project}) ===")
        findings = check_environment(env, project)
        if findings:
            all_findings[env] = findings
            for f in findings:
                print(f"  - {f}")
        else:
            print("  No cost-bearing resources detected.")

    if not all_findings:
        print("\nAll clear.")
        return

    lines = [f":warning: *[コスト警告 {today}] 稼働中リソースあり*", ""]
    for env, findings in all_findings.items():
        lines.append(f"*{env}*")
        for f in findings:
            lines.append(f"  • {f}")
        lines.append("")

    message = "\n".join(lines)
    print(f"\n{message}")

    notify_slack(webhook_url, message)
    print("Slack notification sent.")


if __name__ == "__main__":
    main()
