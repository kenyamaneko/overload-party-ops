import logging

import httpx

logger = logging.getLogger(__name__)


async def _post(response_url: str, text: str, response_type: str) -> None:
    payload = {"response_type": response_type, "text": text}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(response_url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning("Slack response_url returned %s: %s", resp.status_code, resp.text)
        except httpx.HTTPError as e:
            logger.warning("Failed to post to response_url: %s", e)


async def post_ephemeral(response_url: str, text: str) -> None:
    """response_url に ephemeral メッセージを POST する。"""
    await _post(response_url, text, "ephemeral")


async def post_in_channel(response_url: str, text: str) -> None:
    """response_url に in_channel メッセージを POST する。"""
    await _post(response_url, text, "in_channel")
