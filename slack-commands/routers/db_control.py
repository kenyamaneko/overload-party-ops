import logging

from adapters.google_cloud import OperationInProgressError, get_activation_policy, has_pending_update, patch_activation_policy, wait_for_operation
from adapters.slack_response import post_in_channel

logger = logging.getLogger(__name__)

INSTANCE = "overload-party-db"
ENVIRONMENTS: dict[str, str] = {
    "dev": "overload-party-dev",
    "stg": "overload-party-stg",
}


def _parse_env(text: str) -> str | None:
    """コマンド引数から環境名を抽出します。"""
    return text.strip().lower() or None


async def handle_start(response_url: str, text: str) -> None:
    """Cloud SQL インスタンスを起動します。"""
    try:
        env = _parse_env(text)
        if env is None:
            await post_in_channel(response_url, "環境を指定してください: `/db-start dev` or `/db-start stg`")
            return

        project = ENVIRONMENTS.get(env)
        if project is None:
            await post_in_channel(response_url, f"未対応の環境です: `{env}` (dev, stg のみ)")
            return

        # policy=ALWAYS でもオペレーション進行中なら起動途中のため operations.list で確認する
        policy = await get_activation_policy(project, INSTANCE)
        if policy == "ALWAYS":
            if await has_pending_update(project, INSTANCE):
                await post_in_channel(response_url, f":hourglass_flowing_sand: `{env}` の Cloud SQL は起動処理が進行中です。完了までお待ちください。")
            else:
                await post_in_channel(response_url, f":white_check_mark: `{env}` の Cloud SQL は既に起動中です")
            return

        op = await patch_activation_policy(project, INSTANCE, "ALWAYS")
        await post_in_channel(response_url, f":hourglass_flowing_sand: `{env}` の Cloud SQL を起動しています...")

        if await wait_for_operation(project, op):
            await post_in_channel(response_url, f":white_check_mark: `{env}` の Cloud SQL が起動しました")
        else:
            await post_in_channel(response_url, f":warning: `{env}` の Cloud SQL の起動がタイムアウトしました。コンソールを確認してください")

    except OperationInProgressError:
        await post_in_channel(response_url, f":hourglass_flowing_sand: `{env}` の Cloud SQL は別のオペレーションが進行中です。完了後に再度お試しください。")
    except Exception:
        logger.exception("Failed to start Cloud SQL")
        await post_in_channel(response_url, ":warning: Cloud SQL の起動に失敗しました。ログを確認してください。")


async def handle_stop(response_url: str, text: str) -> None:
    """Cloud SQL インスタンスを停止します。"""
    try:
        env = _parse_env(text)
        if env is None:
            await post_in_channel(response_url, "環境を指定してください: `/db-stop dev` or `/db-stop stg`")
            return

        project = ENVIRONMENTS.get(env)
        if project is None:
            await post_in_channel(response_url, f"未対応の環境です: `{env}` (dev, stg のみ)")
            return

        policy = await get_activation_policy(project, INSTANCE)
        if policy == "NEVER":
            if await has_pending_update(project, INSTANCE):
                await post_in_channel(response_url, f":hourglass_flowing_sand: `{env}` の Cloud SQL は停止処理が進行中です。完了までお待ちください。")
            else:
                await post_in_channel(response_url, f":white_check_mark: `{env}` の Cloud SQL は既に停止しています")
            return

        op = await patch_activation_policy(project, INSTANCE, "NEVER")
        await post_in_channel(response_url, f":hourglass_flowing_sand: `{env}` の Cloud SQL を停止しています...")

        if await wait_for_operation(project, op):
            await post_in_channel(response_url, f":octagonal_sign: `{env}` の Cloud SQL を停止しました")
        else:
            await post_in_channel(response_url, f":warning: `{env}` の Cloud SQL の停止がタイムアウトしました。コンソールを確認してください")

    except OperationInProgressError:
        await post_in_channel(response_url, f":hourglass_flowing_sand: `{env}` の Cloud SQL は別のオペレーションが進行中です。完了後に再度お試しください。")
    except Exception:
        logger.exception("Failed to stop Cloud SQL")
        await post_in_channel(response_url, ":warning: Cloud SQL の停止に失敗しました。ログを確認してください。")
