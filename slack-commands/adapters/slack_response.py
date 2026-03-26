import logging

import httpx

logger = logging.getLogger(__name__)


async def _post(response_url: str, text: str, response_type: str) -> None:
    payload = {"response_type": response_type, "text": text}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(response_url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error("Slack response_url returned %s: %s", resp.status_code, resp.text)
    except httpx.HTTPError:
        logger.error("Failed to post to Slack response_url: %s", response_url, exc_info=True)


async def post_ephemeral(response_url: str, text: str) -> None:
    """response_url に ephemeral メッセージを POST する。"""
    await _post(response_url, text, "ephemeral")


async def post_in_channel(response_url: str, text: str) -> None:
    """response_url に in_channel メッセージを POST する。"""
    await _post(response_url, text, "in_channel")
