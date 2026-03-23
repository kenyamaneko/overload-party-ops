import asyncio
import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    html_url: str


async def _fetch_issues(
    client: httpx.AsyncClient, org: str, repo: str,
) -> list[Issue]:
    url = f"{GITHUB_API}/repos/{org}/{repo}/issues"
    params = {"state": "open", "per_page": "100"}
    try:
        resp = await client.get(url, params=params, timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("GitHub API error for %s/%s: %s", org, repo, e)
        return []

    return [
        Issue(number=item["number"], title=item["title"], html_url=item["html_url"])
        for item in resp.json()
        if "pull_request" not in item
    ]


async def fetch_open_issues(
    org: str, repos: list[str],
) -> dict[str, list[Issue]]:
    """全リポジトリの未クローズ Issue を並列取得する。"""
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    async with httpx.AsyncClient(headers=headers) as client:
        tasks = [_fetch_issues(client, org, repo) for repo in repos]
        results = await asyncio.gather(*tasks)

    return {repo: issues for repo, issues in zip(repos, results) if issues}


async def dispatch_workflow(
    org: str, repo: str, workflow_id: str, inputs: dict[str, str],
    *, ref: str = "main",
) -> str | None:
    """GitHub Actions の workflow_dispatch イベントをトリガーする。

    成功時は None、失敗時はエラー詳細の文字列を返す。
    """
    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN is not configured")
        return "GITHUB_TOKEN が未設定です"

    url = f"{GITHUB_API}/repos/{org}/{repo}/actions/workflows/{workflow_id}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }
    body = {"ref": ref, "inputs": inputs}

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=body, headers=headers, timeout=10)
        except httpx.HTTPError as e:
            logger.error("GitHub API error: %s", e)
            return f"GitHub API リクエスト失敗: {e}"

    if resp.status_code == 204:
        return None
    logger.warning("workflow_dispatch failed %s: %s", resp.status_code, resp.text)
    return f"GitHub API HTTP {resp.status_code}: {resp.text}"
