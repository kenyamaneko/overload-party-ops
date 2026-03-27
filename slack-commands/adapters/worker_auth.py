import hmac
import os

from fastapi import HTTPException, Request

# Secret Manager → Cloud Run 環境変数として注入（Terraform: slack_commands/main.tf）
DISPATCH_SECRET = os.environ.get("DISPATCH_SECRET", "")


async def verify_dispatch_request(request: Request) -> bytes:
    """Cloudflare Worker からの転送リクエストを Bearer トークンで認証する。

    認証成功時は raw body を返す（後続で form parse するため）。
    """
    if not DISPATCH_SECRET:
        raise HTTPException(status_code=500, detail="DISPATCH_SECRET is not configured")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Missing bearer token")

    token = auth[len("Bearer "):]
    if not hmac.compare_digest(token, DISPATCH_SECRET):
        raise HTTPException(status_code=403, detail="Invalid token")

    return await request.body()
