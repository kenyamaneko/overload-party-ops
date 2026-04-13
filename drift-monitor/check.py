#!/usr/bin/env python3
"""各リポジトリの Terraform plan を実行し、drift を検出して Slack 通知する。"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

TARGETS_YAML = Path(__file__).parent / "targets.yaml"

GITHUB_ORG = os.environ.get("GITHUB_ORG", "kenyamaneko")
CLONE_BASE = "/tmp/drift-monitor"


def load_targets() -> list[dict]:
    """監視対象の Terraform 環境一覧を読み込みます。"""
    raw = os.environ.get("TARGETS_JSON", "")
    if raw:
        return json.loads(raw)
    if TARGETS_YAML.exists():
        return yaml.safe_load(TARGETS_YAML.read_text())
    return []


def run(args: list[str], cwd: str | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    """外部コマンドを実行します。"""
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=timeout)


def clone_repo(repo: str, token: str) -> str | None:
    """リポジトリを shallow clone します。"""
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
    """terraform init + plan を実行し (exit_code, output) を返します。"""
    init = run(["terraform", "init", "-no-color", "-input=false"], cwd=work_dir, timeout=120)
    if init.returncode != 0:
        return 1, init.stderr.strip() or init.stdout.strip()

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


def _strip_init_noise(output: str) -> str:
    """terraform init のボイラープレート行を除去します。"""
    noise_prefixes = (
        "Initializing the backend",
        "Successfully configured the backend",
        "Initializing provider plugins",
        "- Reusing previous version",
        "- Using previously-installed",
        "- Installing ",
        "- Installed ",
        "Terraform has been successfully initialized",
        "use this backend unless",
    )
    lines = [
        line for line in output.splitlines()
        if line.strip() and not any(line.strip().startswith(p) for p in noise_prefixes)
    ]
    return "\n".join(lines)


def extract_summary(plan_output: str) -> str:
    """plan 出力から Plan: 行と変更対象リソースを抽出します。"""
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


SLACK_TEXT_LIMIT = 3900


def notify_slack(webhook_url: str, message: str) -> None:
    """Slack Webhook にメッセージを送信します。"""
    if len(message) > SLACK_TEXT_LIMIT:
        message = message[:SLACK_TEXT_LIMIT] + "\n…(truncated)"
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
    """全対象環境の terraform plan を実行し、drift を検出して Slack 通知します。"""
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
        print("Error: targets not found (targets.yaml or TARGETS_JSON)", file=sys.stderr)
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
                cleaned = _strip_init_noise(output)
                print(f"error: {cleaned}")
                errors.append({"repo": repo, "env": env_name, "detail": cleaned})

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
