#!/usr/bin/env python3
"""各リポジトリの Terraform plan を実行し、drift を検出して Slack 通知する。"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

GITHUB_ORG = os.environ.get("GITHUB_ORG", "kenyamaneko")
CLONE_BASE = "/tmp/drift-monitor"


def load_targets() -> list[dict]:
    """TARGETS_JSON 環境変数から監視対象を読み込む。

    Terraform 側で定義した targets 変数を jsonencode して渡す想定。
    各要素: {"repo": str, "environments": [{"name": str, "path": str, "project": str}]}
    """
    raw = os.environ.get("TARGETS_JSON", "")
    if not raw:
        return []
    return json.loads(raw)


def run(args: list[str], cwd: str | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=timeout)


def clone_repo(repo: str, token: str) -> str | None:
    dest = f"{CLONE_BASE}/{repo}"
    result = run([
        "git", "clone", "--depth=1",
        f"https://x-access-token:{token}@github.com/{GITHUB_ORG}/{repo}.git",
        dest,
    ])
    if result.returncode != 0:
        print(f"  [clone error] {repo}: {result.stderr.strip()}", file=sys.stderr)
        return None
    return dest


def terraform_plan(work_dir: str) -> tuple[int, str]:
    """terraform init + plan を実行し (exit_code, output) を返す。
    exit_code: 0=差分なし, 2=差分あり, 1=エラー
    """
    init = run(["terraform", "init", "-no-color", "-input=false"], cwd=work_dir, timeout=120)
    if init.returncode != 0:
        return 1, init.stderr.strip()

    plan = run(
        ["terraform", "plan", "-no-color", "-input=false", "-detailed-exitcode", "-lock=false"],
        cwd=work_dir,
        timeout=600,
    )
    output = plan.stdout.strip()
    if plan.returncode == 2:
        return 2, output
    if plan.returncode != 0:
        return 1, plan.stderr.strip() or output
    return 0, output


def extract_summary(plan_output: str) -> str:
    """plan 出力から Plan: 行と変更対象リソースを抽出する。"""
    resources: list[str] = []
    summary_line = ""

    for line in plan_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and "will be" in stripped:
            resources.append(stripped.removeprefix("# "))
        if stripped.startswith("Plan:") or "No changes" in stripped:
            summary_line = stripped

    parts: list[str] = []
    if summary_line:
        parts.append(summary_line)
    if resources:
        parts.append("\n".join(f"  - {r}" for r in resources[:10]))
        if len(resources) > 10:
            parts.append(f"  ...他 {len(resources) - 10} リソース")

    if parts:
        return "\n".join(parts)

    last_lines = plan_output.strip().splitlines()[-5:]
    return "\n".join(last_lines)


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

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("Error: GITHUB_TOKEN is not set", file=sys.stderr)
        sys.exit(1)

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print("Error: SLACK_WEBHOOK_URL is not set", file=sys.stderr)
        sys.exit(1)

    targets = load_targets()
    if not targets:
        print("Error: TARGETS_JSON is not set or empty", file=sys.stderr)
        sys.exit(1)

    os.makedirs(CLONE_BASE, exist_ok=True)

    drifts: list[dict] = []
    errors: list[dict] = []

    for target in targets:
        repo = target["repo"]
        print(f"=== {repo} ===")

        repo_dir = clone_repo(repo, token)
        if repo_dir is None:
            errors.append({"repo": repo, "env": "-", "detail": "clone failed"})
            continue

        for env in target["environments"]:
            env_name = env["name"]
            work_dir = f"{repo_dir}/{env['path']}"
            label = f"{repo}/{env_name}"
            print(f"  [{env_name}] ", end="", flush=True)

            if not os.path.isdir(work_dir):
                print("directory not found, skipping")
                errors.append({"repo": repo, "env": env_name, "detail": "directory not found"})
                continue

            exit_code, output = terraform_plan(work_dir)

            if exit_code == 0:
                print("no drift")
            elif exit_code == 2:
                summary = extract_summary(output)
                print(f"DRIFT DETECTED:\n{summary}")
                drifts.append({"label": label, "summary": summary})
            else:
                print(f"error: {output[:200]}")
                errors.append({"repo": repo, "env": env_name, "detail": output[:200]})

    if not drifts and not errors:
        print("\nAll clear — no drift detected.")
        return

    lines: list[str] = []
    if drifts:
        lines.append(f":rotating_light: *[Terraform Drift {today}] 差分を検出*")
        lines.append("")
        for d in drifts:
            lines.append(f"• *{d['label']}*\n{d['summary']}")
        lines.append("")

    if errors:
        lines.append(f":warning: *[Terraform Drift {today}] plan 実行エラー*")
        lines.append("")
        for e in errors:
            lines.append(f"• *{e['repo']}/{e['env']}*: {e['detail']}")
        lines.append("")

    message = "\n".join(lines)
    print(f"\n{message}")

    notify_slack(webhook_url, message)
    print("Slack notification sent.")


if __name__ == "__main__":
    main()
