import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

SQLADMIN_API = "https://sqladmin.googleapis.com/v1"


async def _get_access_token() -> str:
    """メタデータサーバーからアクセストークンを取得する。"""
    url = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"Metadata-Flavor": "Google"}, timeout=5)
        resp.raise_for_status()
        return resp.json()["access_token"]


async def get_instance_status(project: str, instance: str) -> tuple[str, str]:
    """Cloud SQL インスタンスの activationPolicy と state を返す。"""
    token = await _get_access_token()
    url = f"{SQLADMIN_API}/projects/{project}/instances/{instance}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        policy = data.get("settings", {}).get("activationPolicy", "UNKNOWN")
        state = data.get("state", "UNKNOWN")
        return policy, state


class OperationInProgressError(Exception):
    """Cloud SQL で別のオペレーションが進行中。"""


async def patch_activation_policy(project: str, instance: str, policy: str) -> str:
    """Cloud SQL インスタンスの activationPolicy を変更し、オペレーション名を返す。"""
    token = await _get_access_token()
    url = f"{SQLADMIN_API}/projects/{project}/instances/{instance}"
    body = {"settings": {"activationPolicy": policy}}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.patch(url, json=body, headers=headers, timeout=30)
        if resp.status_code == 409:
            raise OperationInProgressError()
        if resp.status_code >= 400:
            logger.error("Cloud SQL API error %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()

    return resp.json()["name"]


async def wait_for_operation(
    project: str, operation: str, max_attempts: int = 90, interval: int = 10,
) -> bool:
    """Cloud SQL オペレーションが DONE になるまでポーリングする（最大15分）。"""
    url = f"{SQLADMIN_API}/projects/{project}/operations/{operation}"

    for i in range(max_attempts):
        token = await _get_access_token()
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
            resp.raise_for_status()

        data = resp.json()
        status = data.get("status", "")
        logger.info("[poll %d/%d] operation=%s status=%s", i + 1, max_attempts, operation, status)

        if status == "DONE":
            if "error" in data:
                logger.error("Operation failed: %s", data["error"])
                return False
            return True

        await asyncio.sleep(interval)

    return False
