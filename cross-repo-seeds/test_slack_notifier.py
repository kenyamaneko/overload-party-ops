"""cross-repo-seeds/slack_notifier.py のユニットテスト."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

import slack_notifier as sn


class TestActions実行URLの組み立て:
    @pytest.mark.parametrize(
        ("env", "want"),
        [
            pytest.param(
                {
                    "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_REPOSITORY": "org/repo",
                    "GITHUB_RUN_ID": "42",
                },
                "https://github.com/org/repo/actions/runs/42",
                id="全ての環境変数が揃うとき、完全な run URL になる",
            ),
            pytest.param(
                {},
                "",
                id="GITHUB 環境変数が揃わないとき、空文字になる",
            ),
            pytest.param(
                {
                    "GITHUB_SERVER_URL": "https://github.com/",
                    "GITHUB_REPOSITORY": "org/repo",
                    "GITHUB_RUN_ID": "7",
                },
                "https://github.com/org/repo/actions/runs/7",
                id="server_url が末尾スラッシュ付きでも二重スラッシュにならない",
            ),
            pytest.param(
                {
                    "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_REPOSITORY": "org/repo",
                },
                "",
                id="GITHUB_RUN_ID だけ欠けるとき、空文字になる",
            ),
            pytest.param(
                {
                    "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_RUN_ID": "7",
                },
                "",
                id="GITHUB_REPOSITORY だけ欠けるとき、空文字になる",
            ),
            pytest.param(
                {
                    "GITHUB_REPOSITORY": "org/repo",
                    "GITHUB_RUN_ID": "7",
                },
                "",
                id="GITHUB_SERVER_URL だけ欠けるとき、空文字になる",
            ),
        ],
    )
    def test_環境変数からURLを組み立てる(self, env, want):
        with patch.dict(os.environ, env, clear=True):
            assert sn.build_actions_run_url() == want


class TestSlackへの送信:
    def test_JSONのtextペイロードをwebhookに送る(self):
        with patch("slack_notifier.urllib.request.urlopen") as urlopen:
            sn.post_to_slack("https://hooks.slack.com/x", "hello")
        req = urlopen.call_args[0][0]
        assert req.full_url == "https://hooks.slack.com/x"
        assert json.loads(req.data.decode()) == {"text": "hello"}

    def test_上限を超えるメッセージは末尾を切り詰めてtruncateマーカーを付けて送る(self):
        long_msg = "a" * (sn.SLACK_TEXT_LIMIT + 100)
        with patch("slack_notifier.urllib.request.urlopen") as urlopen:
            sn.post_to_slack("https://hooks.slack.com/x", long_msg)
        sent = json.loads(urlopen.call_args[0][0].data.decode())["text"]
        assert sent.endswith("\n…(truncated)")
        assert len(sent) < len(long_msg)


class TestSLACK_WEBHOOK_URLの必須チェック:
    def test_設定済みならその値を返す(self):
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/x"}, clear=True):
            assert sn.require_webhook_url() == "https://hooks.slack.com/x"

    def test_未設定のときexit1で落とす(self, capsys):
        # webhook 未設定は通知経路が無い異常。silent skip せず exit 1 で気付かせる。
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as exc:
                sn.require_webhook_url()
        assert exc.value.code == 1
        assert "SLACK_WEBHOOK_URL is not set" in capsys.readouterr().err
