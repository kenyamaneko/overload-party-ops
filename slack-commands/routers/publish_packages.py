import logging

from adapters.github import dispatch_workflow
from adapters.slack_response import post_in_channel

logger = logging.getLogger(__name__)

GITHUB_ORG = "kenyamaneko"
COMMON_REPO = "overload-party-common"
WORKFLOW_ID = "publish-packages.yaml"
DEFAULT_REF = "main"


async def handle(response_url: str, text: str) -> None:
    try:
        ref = text.strip() or DEFAULT_REF

        ok = await dispatch_workflow(
            GITHUB_ORG, COMMON_REPO, WORKFLOW_ID, {},
            ref=ref,
        )
        if ok:
            await post_in_channel(
                response_url,
                f"`{COMMON_REPO}` の `{ref}` ブランチからパッケージ publish ワークフローをディスパッチしました。",
            )
        else:
            await post_in_channel(
                response_url,
                "パッケージ publish ワークフローのディスパッチに失敗しました。ログを確認してください。",
            )

    except Exception:
        logger.exception("Failed to dispatch publish-packages")
        await post_in_channel(
            response_url,
            "パッケージ publish ワークフローのディスパッチに失敗しました。ログを確認してください。",
        )
