#!/usr/bin/env python3
"""各リポジトリの Terraform plan を実行し、drift を検出して Slack 通知する。

targets.yaml の各 env に `suppress:` を書くと、特定リソース型の特定属性差分を
drift としてカウントしないようにできる。dev/stg の Cloud SQL activation_policy の
ように「運用上 Terraform 外から頻繁に書き換わる属性」をここで抑止する。prod
の意図しない変更を検知したいので、ignore_changes で Terraform 側に載せるのは
避け、検知器側で環境ごとに抑止する設計。
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

TARGETS_YAML = Path(__file__).parent / "targets.yaml"

GITHUB_ORG = os.environ.get("GITHUB_ORG", "kenyamaneko")
CLONE_BASE = "/tmp/drift-monitor"
PLAN_FILE = "drift-monitor.tfplan"


def load_targets() -> list[dict]:
    """監視対象の Terraform 環境一覧を読み込みます。"""
    if not TARGETS_YAML.exists():
        raise FileNotFoundError(f"targets file not found: {TARGETS_YAML}")
    return yaml.safe_load(TARGETS_YAML.read_text())


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


def run_terraform_plan(work_dir: str) -> tuple[int, str]:
    """terraform init + plan (+ show -json) を実行し (exit_code, output) を返します。

    exit_code と output の意味:
      0 = 差分なし、output は空文字
      2 = drift あり、output は `terraform show -json` の JSON 文字列
      1 = init / plan / show いずれかの失敗、output はエラー詳細

    drift 時に JSON を返すのは、上位で属性レベルの suppress 判定を行うため。
    """
    init = run(["terraform", "init", "-no-color", "-input=false"], cwd=work_dir, timeout=120)
    if init.returncode != 0:
        return 1, init.stderr.strip() or init.stdout.strip()

    plan_path = os.path.join(work_dir, PLAN_FILE)
    plan = run(
        [
            "terraform", "plan", "-no-color", "-input=false",
            "-detailed-exitcode", "-lock=false", f"-out={plan_path}",
        ],
        cwd=work_dir,
        timeout=600,
    )
    if plan.returncode == 0:
        return 0, ""
    if plan.returncode != 2:
        return 1, plan.stderr.strip() or plan.stdout.strip()

    show = run(
        ["terraform", "show", "-json", plan_path],
        cwd=work_dir,
        timeout=120,
    )
    if show.returncode != 0:
        return 1, show.stderr.strip() or show.stdout.strip()
    return 2, show.stdout


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


class PlanParseError(Exception):
    """terraform plan JSON のパース / サマリ組立に失敗したことを示す。"""


def _diff_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """before と after を再帰比較し、差分が出ている属性パスを列挙する。

    list は `[i]`、dict は `.key` で連結する（例: `settings[0].activation_policy`）。
    両側で等しい部分木は返さず、型が揃わない場合は現在の prefix をそのまま差分とみなす。
    """
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        paths: list[str] = []
        for key in set(before.keys()) | set(after.keys()):
            sub = f"{prefix}.{key}" if prefix else key
            paths.extend(_diff_paths(before.get(key), after.get(key), sub))
        return paths
    if isinstance(before, list) and isinstance(after, list):
        paths = []
        for i in range(max(len(before), len(after))):
            b = before[i] if i < len(before) else None
            a = after[i] if i < len(after) else None
            paths.extend(_diff_paths(b, a, f"{prefix}[{i}]"))
        return paths
    return [prefix] if prefix else []


def _is_resource_suppressed(resource_change: dict, suppress_rules: list[dict]) -> bool:
    """リソースの update 差分が suppress_rules の範囲内で全て説明つくかを判定する。

    update 以外（create/delete/replace）は構造的変更なのでノイズ扱いしない。
    update の場合、変化している属性パスが全て suppress rule の (type, attribute) に
    当てはまっていれば True。
    """
    change = resource_change.get("change", {})
    actions = change.get("actions", [])
    if actions != ["update"]:
        return False

    res_type = resource_change.get("type", "")
    matching_attrs = {
        rule["attribute"] for rule in suppress_rules
        if rule.get("type") == res_type
    }
    if not matching_attrs:
        return False

    changed_paths = _diff_paths(change.get("before"), change.get("after"))
    if not changed_paths:
        return False
    return all(path in matching_attrs for path in changed_paths)


def parse_plan_json(plan_json: str, suppress_rules: list[dict]) -> tuple[list[dict], list[dict]]:
    """plan JSON から no-op 以外のリソース変更を取り出し、suppress 対象を分離する。

    Returns:
      (visible_changes, suppressed_changes): 前者だけが Slack 通知の対象。
    """
    try:
        data = json.loads(plan_json)
    except json.JSONDecodeError as e:
        raise PlanParseError(f"plan JSON の decode に失敗: {e}")

    resource_changes = data.get("resource_changes", [])
    visible: list[dict] = []
    suppressed: list[dict] = []

    for rc in resource_changes:
        actions = rc.get("change", {}).get("actions", [])
        # actions=["read"] は data source が plan で確定できず apply 時に再 read される
        # ことを示す派生イベント。トリガとなった managed resource の変更が同じ plan に
        # 必ず併載されるため、read 自体を drift として通知しない (terraform CLI の
        # "Plan: X to add, Y to change, Z to destroy" カウントが read を含めないのと同じ)。
        if not actions or actions == ["no-op"] or actions == ["read"]:
            continue
        if _is_resource_suppressed(rc, suppress_rules):
            suppressed.append(rc)
        else:
            visible.append(rc)
    return visible, suppressed


_ACTION_TO_LABEL = {
    ("create",): "created",
    ("update",): "updated in-place",
    ("delete",): "destroyed",
    ("delete", "create"): "replaced",
    ("create", "delete"): "replaced",
}


def format_action_label(actions: list[str]) -> str:
    """resource_changes[].change.actions を terraform plan のテキスト表現に寄せる。"""
    label = _ACTION_TO_LABEL.get(tuple(actions))
    if label is None:
        raise PlanParseError(f"未知の actions: {actions}")
    return label


def _count_actions(visible_changes: list[dict]) -> dict[str, int]:
    """visible_changes から add/change/destroy の件数を集計する。"""
    counts = {"add": 0, "change": 0, "destroy": 0}
    buckets = {
        ("create",): ("add",),
        ("update",): ("change",),
        ("delete",): ("destroy",),
        ("delete", "create"): ("add", "destroy"),
        ("create", "delete"): ("add", "destroy"),
    }
    for rc in visible_changes:
        actions = tuple(rc.get("change", {}).get("actions", []))
        for bucket in buckets.get(actions, ()):
            counts[bucket] += 1
    return counts


def format_summary(visible_changes: list[dict]) -> str:
    """visible_changes を Slack 通知用サマリ文字列に整形する。

    terraform plan の人間可読テキストに合わせ "Plan: X to add, Y to change, Z to destroy"
    と変更対象リソース一覧（先頭 10 件 + 超過分は集約行）を組み立てる。
    visible_changes が空で呼ばれるのは想定外のため PlanParseError を投げる。
    """
    if not visible_changes:
        raise PlanParseError("visible_changes が空のまま format_summary が呼ばれた")

    counts = _count_actions(visible_changes)
    plan_line = (
        f"Plan: {counts['add']} to add, "
        f"{counts['change']} to change, "
        f"{counts['destroy']} to destroy."
    )

    resource_lines = [
        f"{rc.get('address', '')} will be {format_action_label(rc['change']['actions'])}"
        for rc in visible_changes
    ]

    parts = [plan_line]
    parts.append("\n".join(f"  - {r}" for r in resource_lines[:10]))
    if len(resource_lines) > 10:
        parts.append(f"  ...他 {len(resource_lines) - 10} リソース")
    return "\n".join(parts)


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
            suppress_rules = env.get("suppress", [])
            print(f"  [{env_name}] ", end="", flush=True)

            if not os.path.isdir(work_dir):
                print("directory not found, skipping")
                errors.append({"repo": repo, "env": env_name, "detail": "directory not found"})
                continue

            exit_code, output = run_terraform_plan(work_dir)

            if exit_code == 0:
                print("no drift")
                continue
            if exit_code != 2:
                cleaned = _strip_init_noise(output)
                print(f"error: {cleaned}")
                errors.append({"repo": repo, "env": env_name, "detail": cleaned})
                continue

            try:
                visible, suppressed = parse_plan_json(output, suppress_rules)
            except PlanParseError as e:
                # パース失敗を fallback で隠すと「drift あり、内容不明」のミスリード
                # 通知になるためエラー扱いにして人間の調査を促す
                print(f"DRIFT DETECTED but parse failed: {e}")
                errors.append({"repo": repo, "env": env_name, "detail": f"plan JSON のパース失敗: {e}"})
                continue

            if not visible:
                print(f"no drift (suppressed {len(suppressed)} change(s))")
                continue

            try:
                summary = format_summary(visible)
            except PlanParseError as e:
                print(f"DRIFT DETECTED but summary build failed: {e}")
                errors.append({"repo": repo, "env": env_name, "detail": f"summary 組立失敗: {e}"})
                continue

            suppressed_note = f" ({len(suppressed)} suppressed)" if suppressed else ""
            print(f"DRIFT DETECTED{suppressed_note}:\n{summary}")
            drifts.append({"label": label, "summary": summary})

    lines: list[str] = []
    if drifts:
        lines.append(f":rotating_light: *[ドリフト警告 {today}] 差分を検出*")
        lines.append("")
        for d in drifts:
            lines.append(f"• *{d['label']}*\n{d['summary']}")
        lines.append("")

    if errors:
        lines.append(f":warning: *[ドリフト確認 {today}] plan 実行エラー*")
        lines.append("")
        for e in errors:
            lines.append(f"• *{e['repo']}/{e['env']}*: {e['detail']}")
        lines.append("")

    # 全環境クリアでも 1 通送り、通知の無音をジョブ停止と区別できるようにする
    if not drifts and not errors:
        lines.append(f":white_check_mark: *[ドリフト確認 {today}] 差分なし*")

    message = "\n".join(lines)
    print(f"\n{message}")

    notify_slack(webhook_url, message)
    print("Slack notification sent.")


if __name__ == "__main__":
    main()
