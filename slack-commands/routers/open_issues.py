import json
import logging
import os

from adapters.github import OpenIssuesResult, fetch_open_issues
from adapters.slack_response import post_ephemeral

logger = logging.getLogger(__name__)

GITHUB_ORG = "kenyamaneko"
DEFAULT_REPOS = [
    "overload-party-common",
    "overload-party-client",
    "overload-party-battle",
    "overload-party-gateway",
    "overload-party-account",
    "overload-party-card",
    "overload-party-matchmaking",
    "overload-party-shop",
    "overload-party-scenario",
    "overload-party-infra",
    "overload-party-k8s",
    "overload-party-newsfeed",
    "overload-party-analytics",
    "overload-party-ops",
]


def _load_repos() -> list[str]:
    raw = os.environ.get("REPOS_JSON", "")
    return json.loads(raw) if raw else DEFAULT_REPOS


def _format_response(result: OpenIssuesResult) -> str:
    lines: list[str] = []

    if result.failed_repos:
        lines.append(f":warning: {len(result.failed_repos)} リポジトリで取得に失敗しました\n")
        for repo, error in result.failed_repos.items():
            lines.append(f"  • *{repo}*: `{error}`")
        lines.append("")

    if result.issues_by_repo:
        total = sum(len(issues) for issues in result.issues_by_repo.values())
        lines.append(f":mag: 未クローズの Issue (全 {total} 件)\n")
        for repo, issues in result.issues_by_repo.items():
            lines.append(f"*{repo}* ({len(issues)} 件)")
            for issue in issues:
                lines.append(f"  • <{issue.html_url}|{issue.title}>")
            lines.append("")
    elif not result.failed_repos:
        lines.append(":white_check_mark: 未クローズの Issue はありません")

    return "\n".join(lines)


async def handle(response_url: str, text: str = "") -> None:
    """バックグラウンドで GitHub API を呼び出し、結果を response_url に返す。"""
    try:
        repos = _load_repos()
        result = await fetch_open_issues(GITHUB_ORG, repos)
        text = _format_response(result)
    except Exception:
        logger.exception("Failed to fetch open issues")
        text = ":warning: Issue の取得に失敗しました。ログを確認してください。"
    await post_ephemeral(response_url, text)
