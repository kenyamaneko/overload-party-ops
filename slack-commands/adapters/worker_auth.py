import hmac
import os

from fastapi import HTTPException, Request

DISPATCH_SECRET = os.environ.get("DISPATCH_SECRET", "")


async def verify_dispatch_request(request: Request) -> bytes:
    """Cloudflare Worker からの転送リクエストを Bearer トークンで認証します。"""
    if not DISPATCH_SECRET:
        raise HTTPException(status_code=500, detail="DISPATCH_SECRET is not configured")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Missing bearer token")

    token = auth[len("Bearer "):]
    if not hmac.compare_digest(token, DISPATCH_SECRET):
        raise HTTPException(status_code=403, detail="Invalid token")

    return await request.body()
