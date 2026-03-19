import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request


SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
_MAX_AGE_SECONDS = 5 * 60


async def verify_slack_request(request: Request) -> bytes:
    """Slack Signing Secret による署名検証。FastAPI Depends() で使う。

    検証成功時は raw body を返す（後続で form parse するため）。
    """
    if not SLACK_SIGNING_SECRET:
        raise HTTPException(status_code=500, detail="SLACK_SIGNING_SECRET is not configured")

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not timestamp or not signature:
        raise HTTPException(status_code=403, detail="Missing Slack signature headers")

    if abs(time.time() - int(timestamp)) > _MAX_AGE_SECONDS:
        raise HTTPException(status_code=403, detail="Request too old")

    body = await request.body()
    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), base.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    return body
