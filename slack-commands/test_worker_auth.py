#!/usr/bin/env python3
"""Cloudflare Worker → Cloud Run 間の Bearer 認証仕様を固定するテスト。

セキュリティ境界なので挙動の変化を必ず検知したい:
  - DISPATCH_SECRET 未設定 → 500 (設定ミスを silent に「認証スキップ」しない)
  - Authorization ヘッダ無し / Bearer プレフィックス欠落 → 403
  - トークン不一致 → 403 (timing-safe 比較使用)
  - トークン一致 → None (認証成功)

純粋関数 check_bearer を直接テストするため fastapi に依存しない。
"""
import importlib.util
from pathlib import Path

# fastapi が未インストールでもテストを動かすため、worker_auth をパッケージ
# 経由 (adapters.worker_auth) ではなくファイル直読みで load する。
# パッケージ経由の import だと adapters/__init__.py → fastapi 依存の他モジュール
# まで連鎖的に読み込まれる可能性がある。
_WORKER_AUTH_PATH = Path(__file__).parent / "adapters" / "worker_auth.py"
_spec = importlib.util.spec_from_file_location("worker_auth_under_test", _WORKER_AUTH_PATH)
worker_auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker_auth)

check_bearer = worker_auth.check_bearer


class TestCheckBearer:
    def test_missing_secret_returns_500(self):
        """観点: サーバー側 DISPATCH_SECRET 未設定は設定ミスなので 500。

        403 にすると「Worker 側のトークンが間違っている」と誤診されるため、
        500 + "not configured" で設定ミスと明示する仕様。
        """
        result = check_bearer("Bearer anything", "")
        assert result is not None
        status, detail = result
        assert status == 500
        assert "not configured" in detail

    def test_missing_auth_header_returns_403(self):
        """観点: Authorization ヘッダが無ければ 403。"""
        result = check_bearer("", "s3cret")
        assert result == (403, "Missing bearer token")

    def test_non_bearer_scheme_returns_403(self):
        """観点: Bearer 以外のスキーム（Basic 等）を受け付けない。"""
        result = check_bearer("Basic dXNlcjpwYXNz", "s3cret")
        assert result is not None
        assert result[0] == 403

    def test_wrong_token_returns_403(self):
        """観点: トークン不一致は 403 + "Invalid token"。"""
        result = check_bearer("Bearer wrong-token", "s3cret")
        assert result == (403, "Invalid token")

    def test_valid_token_returns_none(self):
        """観点: トークン一致時は None（= 認証成功シグナル）を返す。"""
        assert check_bearer("Bearer s3cret", "s3cret") is None

    def test_token_prefix_match_is_rejected(self):
        """観点: 正しいトークンで始まる別トークンを拒否する（prefix match 脆弱性を避ける）。

        hmac.compare_digest を使っていることの挙動確認。"s3cret" で始まる
        "s3cretExtra" が区別されることを保証する仕様。
        """
        result = check_bearer("Bearer s3cretExtra", "s3cret")
        assert result == (403, "Invalid token")

    def test_empty_token_after_bearer_is_rejected(self):
        """観点: "Bearer " の後が空でも 403（空トークンを成功扱いにしない）。

        DISPATCH_SECRET が誤って空文字列と比較されるリスクを避けるための保全。
        """
        result = check_bearer("Bearer ", "s3cret")
        assert result == (403, "Invalid token")

    def test_500_is_checked_before_403(self):
        """観点: secret 未設定は「認証失敗」より先に返される（設定ミスを隠さない）。

        もし auth チェックが先だと、クライアントは「トークンが間違っている」と
        勘違いする。設定ミスを最優先で明示する仕様の固定。
        """
        # auth_header が空でも secret 未設定を先に返す
        result = check_bearer("", "")
        assert result is not None
        assert result[0] == 500
