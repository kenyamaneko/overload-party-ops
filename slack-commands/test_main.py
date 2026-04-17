#!/usr/bin/env python3
"""FastAPI 層の統合テスト。認証層 → 受付レスポンス → handler 起動までを固定する。

test_worker_auth.py は check_bearer の純粋ロジックを固定している。このファイルでは
「その判定結果が確かに HTTP 403/500 として Cloudflare Worker に返る」ことを
確認する。純粋ロジックのテストだけだと、例えば main.py 側で Depends 配線を
外してしまっても気付けない（認証が bypass される致命的バグ）。
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client_with_secret():
    """DISPATCH_SECRET="s3cret" でテスト。"""
    with patch("adapters.worker_auth.DISPATCH_SECRET", "s3cret"):
        yield TestClient(app)


@pytest.fixture
def client_without_secret():
    """DISPATCH_SECRET="" (設定ミス) でテスト。"""
    with patch("adapters.worker_auth.DISPATCH_SECRET", ""):
        yield TestClient(app)


@pytest.fixture(autouse=True)
def _silence_background_tasks():
    """background_tasks 経由で呼ばれる副作用（Slack 通知・workflow dispatch）を全部モック。

    auth 層と受付レスポンスのテストだけが目的で、下流の副作用は別テストでカバー済。
    """
    with patch("adapters.slack_response.post_in_channel", new=AsyncMock()), \
         patch("routers.db_control.post_in_channel", new=AsyncMock()), \
         patch("routers.db_control.dispatch_workflow", new=AsyncMock(return_value=None)), \
         patch("routers.gke_control.post_in_channel", new=AsyncMock()), \
         patch("routers.gke_control.dispatch_workflow", new=AsyncMock(return_value=None)):
        yield


class TestHealth:
    """/health は認証不要で 200 を返す（Cloud Run のヘルスチェック用）。"""

    def test_health_returns_ok(self, client_with_secret):
        resp = client_with_secret.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_accessible_without_auth(self, client_with_secret):
        """観点: Authorization ヘッダ無しでも /health は 200。"""
        resp = client_with_secret.get("/health")
        assert resp.status_code == 200


class TestSlackCommandsAuth:
    """/slack/commands の認証層が HTTP レスポンスに反映されることを固定。

    check_bearer の判定結果が「本当に 403/500 として返る」経路の結合テスト。
    これが無いと main.py で `Depends(verify_dispatch_request)` を外された場合に
    全ての認証が bypass されても既存テストでは検知できない。
    """

    def test_missing_auth_header_returns_403(self, client_with_secret):
        """観点: Authorization 欠落 → 403。"""
        resp = client_with_secret.post(
            "/slack/commands",
            data={"command": "/db-start", "text": "dev"},
        )
        assert resp.status_code == 403

    def test_wrong_bearer_returns_403(self, client_with_secret):
        """観点: 間違った Bearer トークン → 403。"""
        resp = client_with_secret.post(
            "/slack/commands",
            data={"command": "/db-start", "text": "dev"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 403

    def test_non_bearer_scheme_returns_403(self, client_with_secret):
        """観点: Basic 等の別スキーム → 403。"""
        resp = client_with_secret.post(
            "/slack/commands",
            data={"command": "/db-start", "text": "dev"},
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 403

    def test_valid_bearer_returns_200(self, client_with_secret):
        """観点: 正しい Bearer → 200 (accepted)。

        Slack の 3 秒応答制約を守るため、handler 実行は background に回して
        即座に 200 を返すのが仕様。
        """
        resp = client_with_secret.post(
            "/slack/commands",
            data={
                "command": "/db-start",
                "text": "dev",
                "response_url": "https://slack/response",
            },
            headers={"Authorization": "Bearer s3cret"},
        )
        assert resp.status_code == 200

    def test_secret_not_configured_returns_500(self, client_without_secret):
        """観点: サーバー側 DISPATCH_SECRET 未設定は 500 として 403 と区別する。

        403 にすると「Worker のトークン間違い」と誤診され、実際の根本原因
        （サーバー側設定漏れ）に辿り着けない。設定ミスは 500 で明示する仕様。
        """
        resp = client_without_secret.post(
            "/slack/commands",
            data={"command": "/db-start", "text": "dev"},
            headers={"Authorization": "Bearer anything"},
        )
        assert resp.status_code == 500


class TestSlackCommandsAcceptance:
    """認証通過後の受付挙動。Slack 3 秒応答を守るための「即 200 + 非同期実行」仕様。"""

    def test_unknown_command_returns_200(self, client_with_secret):
        """観点: 未対応コマンドでも 200 で受け取る（エラー通知は response_url 経由）。

        FastAPI が 4xx/5xx を返すと Slack UI に汎用エラーが出てしまい、
        「何が未対応なのか」ユーザーが分からない。200 で受けて response_url に
        「未対応のコマンドです」を流すのが仕様。
        """
        resp = client_with_secret.post(
            "/slack/commands",
            data={"command": "/nonexistent", "response_url": "https://slack/url"},
            headers={"Authorization": "Bearer s3cret"},
        )
        assert resp.status_code == 200

    def test_missing_response_url_returns_200(self, client_with_secret):
        """観点: response_url が欠けていても 200 は返す（Slack 側エラー UI に委ねる）。

        response_url 欠落は Slack からのリクエストとしては異常だが、Cloud Run 側で
        エラーを返すと Slack UI が「タイムアウト」を出すためユーザー混乱を招く。
        """
        resp = client_with_secret.post(
            "/slack/commands",
            data={"command": "/db-start", "text": "dev"},
            headers={"Authorization": "Bearer s3cret"},
        )
        assert resp.status_code == 200
