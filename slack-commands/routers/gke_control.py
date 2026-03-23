import logging

from adapters.github import dispatch_workflow
from adapters.slack_response import post_in_channel

logger = logging.getLogger(__name__)

# GitHub Actions workflow_dispatch で GKE 操作を実行する（ADR-009）。
# Cloud Run に gcloud/kubectl を入れるとイメージが 500MB+ 増加するため、
# Slack コマンドはワークフローのトリガーのみ行い、実処理は GitHub Actions に委譲する。
GITHUB_ORG = "kenyamaneko"
K8S_REPO = "overload-party-k8s"
WORKFLOW_ID = "env-lifecycle.yaml"
ENVIRONMENTS = {"dev", "stg"}


def _parse_env(text: str) -> str | None:
    return text.strip().lower() or None


async def handle_up(response_url: str, text: str) -> None:
    try:
        env = _parse_env(text)
        if env is None:
            await post_in_channel(response_url, "環境を指定してください: `/gke-up dev` or `/gke-up stg`")
            return

        if env not in ENVIRONMENTS:
            await post_in_channel(response_url, f"未対応の環境です: `{env}` (dev, stg のみ)")
            return

        error = await dispatch_workflow(
            GITHUB_ORG, K8S_REPO, WORKFLOW_ID,
            {"action": "up", "environment": env, "slack_notification": "true"},
        )
        if error is None:
            await post_in_channel(response_url, f":rocket: `{env}` の GKE 起動ワークフローをディスパッチしました。完了時に通知されます。")
        else:
            await post_in_channel(response_url, f":warning: GKE 起動ワークフローのディスパッチに失敗しました。\n```{error}```")

    except Exception:
        logger.exception("Failed to dispatch gke-up")
        await post_in_channel(response_url, ":warning: GKE 起動ワークフローのディスパッチに失敗しました。ログを確認してください。")


async def handle_down(response_url: str, text: str) -> None:
    try:
        env = _parse_env(text)
        if env is None:
            await post_in_channel(response_url, "環境を指定してください: `/gke-down dev` or `/gke-down stg`")
            return

        if env not in ENVIRONMENTS:
            await post_in_channel(response_url, f"未対応の環境です: `{env}` (dev, stg のみ)")
            return

        error = await dispatch_workflow(
            GITHUB_ORG, K8S_REPO, WORKFLOW_ID,
            {"action": "down", "environment": env, "slack_notification": "true"},
        )
        if error is None:
            await post_in_channel(response_url, f":rocket: `{env}` の GKE 停止ワークフローをディスパッチしました。完了時に通知されます。")
        else:
            await post_in_channel(response_url, f":warning: GKE 停止ワークフローのディスパッチに失敗しました。\n```{error}```")

    except Exception:
        logger.exception("Failed to dispatch gke-down")
        await post_in_channel(response_url, ":warning: GKE 停止ワークフローのディスパッチに失敗しました。ログを確認してください。")
