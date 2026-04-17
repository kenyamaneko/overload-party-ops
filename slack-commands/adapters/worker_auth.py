import hmac
import os

from fastapi import HTTPException, Request

DISPATCH_SECRET = os.environ.get("DISPATCH_SECRET", "")

# 500 = サーバー設定ミス（DISPATCH_SECRET 未設定）、403 = クライアント認証失敗。
# この区別が崩れると運用時に「Worker 側のトークン間違い」と「Cloud Run 側の
# 設定漏れ」を混同するため、純粋ロジックを分離してテスト可能にしている。
BearerError = tuple[int, str]  # (status_code, detail)


def check_bearer(auth_header: str, expected_secret: str) -> BearerError | None:
    """Bearer 認証を検証し、失敗時は (status_code, detail) を、成功時は None を返します。"""
    if not expected_secret:
        return 500, "DISPATCH_SECRET is not configured"
    if not auth_header.startswith("Bearer "):
        return 403, "Missing bearer token"
    token = auth_header[len("Bearer "):]
    if not hmac.compare_digest(token, expected_secret):
        return 403, "Invalid token"
    return None


async def verify_dispatch_request(request: Request) -> bytes:
    """Cloudflare Worker からの転送リクエストを Bearer トークンで認証します。"""
    auth = request.headers.get("Authorization", "")
    error = check_bearer(auth, DISPATCH_SECRET)
    if error is not None:
        status_code, detail = error
        raise HTTPException(status_code=status_code, detail=detail)
    return await request.body()
