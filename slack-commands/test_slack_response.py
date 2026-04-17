#!/usr/bin/env python3
"""adapters/slack_response.py の response_url への POST 挙動を固定するテスト。

Slack 通知は唯一のユーザー可視化経路だが、通信失敗でハンドラ本体を
落とすと後続処理（dispatch 完了通知など）が全て失敗する。そのため
httpx エラーは握りつぶさず「ログには必ず残し、例外は上げない」仕様。

この「ログには残す」側のテストが抜けると、次のリファクタで
`except: pass` に退化しても気付けない。
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from adapters.slack_response import post_ephemeral, post_in_channel


def run(coro):
    return asyncio.run(coro)


def _mock_client(status_code: int, text: str = "") -> MagicMock:
    """httpx.AsyncClient() as client の形をそのまま返す MagicMock。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestResponseType:
    """response_type を in_channel / ephemeral で正しく使い分ける仕様。"""

    def test_in_channel_uses_in_channel_type(self):
        """観点: post_in_channel は response_type="in_channel" で POST する。"""
        client = _mock_client(200)
        with patch("adapters.slack_response.httpx.AsyncClient", return_value=client):
            run(post_in_channel("https://slack/url", "hello"))
        kwargs = client.post.call_args.kwargs
        assert kwargs["json"]["response_type"] == "in_channel"
        assert kwargs["json"]["text"] == "hello"

    def test_ephemeral_uses_ephemeral_type(self):
        """観点: post_ephemeral は response_type="ephemeral" で POST する。"""
        client = _mock_client(200)
        with patch("adapters.slack_response.httpx.AsyncClient", return_value=client):
            run(post_ephemeral("https://slack/url", "hi"))
        kwargs = client.post.call_args.kwargs
        assert kwargs["json"]["response_type"] == "ephemeral"
        assert kwargs["json"]["text"] == "hi"

    def test_post_uses_response_url_path(self):
        """観点: POST 先 URL は引数の response_url がそのまま使われる。"""
        client = _mock_client(200)
        with patch("adapters.slack_response.httpx.AsyncClient", return_value=client):
            run(post_in_channel("https://slack/specific-url", "hi"))
        args, _ = client.post.call_args
        assert args[0] == "https://slack/specific-url"


class TestFailureHandling:
    """通信失敗時の挙動。ログ必須、例外は握る（呼び出し側を継続させる）。"""

    def test_http_error_does_not_raise(self):
        """観点: httpx エラーでも呼び出し側に例外を上げない（ハンドラ継続）。

        Slack への POST 失敗でハンドラ本体を落とすと、ワークフロー dispatch 成功通知
        が届かず、ユーザーには「反応なし」に見える。通信失敗はログ残して続行が仕様。
        """
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.HTTPError("unreachable"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.slack_response.httpx.AsyncClient", return_value=client):
            # 例外が出ないこと
            run(post_in_channel("https://slack/url", "hello"))

    def test_http_error_logged_with_response_url(self, caplog):
        """観点: httpx 失敗は error ログに残し、response_url を含めて追跡可能にする。

        ログが残らないと通知ロスに気付けない。silent failure の典型ケース。
        """
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.HTTPError("unreachable"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch("adapters.slack_response.httpx.AsyncClient", return_value=client), \
             caplog.at_level(logging.ERROR, logger="adapters.slack_response"):
            run(post_in_channel("https://slack/specific-url", "hello"))

        assert any("slack/specific-url" in rec.getMessage() for rec in caplog.records)

    def test_non_200_status_logs_error(self, caplog):
        """観点: Slack が non-200 を返した場合も error ログに残す（silent 成功扱いしない）。

        httpx 通信自体は成功していても、response_url 期限切れ（410）や Slack 側障害（5xx）
        で通知が届いていない状況を検知するため、必ず status code をログに残す。
        """
        client = _mock_client(500, text="internal error")
        with patch("adapters.slack_response.httpx.AsyncClient", return_value=client), \
             caplog.at_level(logging.ERROR, logger="adapters.slack_response"):
            run(post_in_channel("https://slack/url", "hello"))

        assert any("500" in rec.getMessage() for rec in caplog.records)

    def test_non_200_does_not_raise(self):
        """観点: non-200 ステータスでも呼び出し側に例外を上げない。"""
        client = _mock_client(410, text="expired")
        with patch("adapters.slack_response.httpx.AsyncClient", return_value=client):
            # 例外が出ないこと
            run(post_in_channel("https://slack/url", "hello"))
