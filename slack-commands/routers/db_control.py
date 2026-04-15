import logging

from adapters.github import dispatch_workflow
from adapters.slack_response import post_in_channel

logger = logging.getLogger(__name__)

# ADR「ノードプールスケーリング戦略とGKEの所有権」の所有権原則に従い、
# Cloud SQL の実処理は overload-party-infra (DB オーナー) に委譲する。
# 旧実装は本ルーター内で Google Cloud SQL API を直接叩いていたが、
# 所有権越境を避けるため workflow_dispatch でオーナーリポの workflow を
# 呼ぶ形に統一した。完了通知はワークフロー側から Slack に送られる。
GITHUB_ORG = "kenyamaneko"
INFRA_REPO = "overload-party-infra"
WORKFLOW_ID = "cloudsql-activation.yaml"
ENVIRONMENTS = {"dev", "stg"}


def _parse_env(text: str) -> str | None:
    return text.strip().lower() or None


async def _handle(response_url: str, text: str, action: str, action_jp: str) -> None:
    try:
        env = _parse_env(text)
        if env is None:
            await post_in_channel(
                response_url,
                f"環境を指定してください: `/db-{action} dev` or `/db-{action} stg`",
            )
            return

        if env not in ENVIRONMENTS:
            await post_in_channel(response_url, f"未対応の環境です: `{env}` (dev, stg のみ)")
            return

        error = await dispatch_workflow(
            GITHUB_ORG, INFRA_REPO, WORKFLOW_ID,
            {"action": action, "environment": env, "slack_notification": "true"},
        )
        if error is None:
            await post_in_channel(
                response_url,
                f":rocket: `{env}` の Cloud SQL {action_jp}ワークフローをディスパッチしました。完了時に通知されます。",
            )
        else:
            await post_in_channel(
                response_url,
                f":warning: Cloud SQL {action_jp}ワークフローのディスパッチに失敗しました。\n```{error}```",
            )

    except Exception:
        logger.exception("Failed to dispatch cloudsql-activation action=%s", action)
        await post_in_channel(
            response_url,
            f":warning: Cloud SQL {action_jp}ワークフローのディスパッチに失敗しました。ログを確認してください。",
        )


async def handle_start(response_url: str, text: str) -> None:
    """Cloud SQL 起動ワークフローをディスパッチします。"""
    await _handle(response_url, text, action="up", action_jp="起動")


async def handle_stop(response_url: str, text: str) -> None:
    """Cloud SQL 停止ワークフローをディスパッチします。"""
    await _handle(response_url, text, action="down", action_jp="停止")
