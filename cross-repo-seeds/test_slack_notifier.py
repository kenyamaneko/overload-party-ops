"""cross-repo-seeds/slack_notifier.py のユニットテスト."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

import slack_notifier as sn

TRUNCATION_BOUNDARY_LENGTH = 40000


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

    def test_本文が40000文字ちょうどのとき送信される本文は元のメッセージと一致する(self):
        message = "a" * TRUNCATION_BOUNDARY_LENGTH
        with patch("slack_notifier.urllib.request.urlopen") as urlopen:
            sn.post_to_slack("https://hooks.slack.com/x", message)
        sent = json.loads(urlopen.call_args[0][0].data.decode())["text"]
        assert sent == message

    def test_本文が40001文字のとき送信される本文は末尾が打ち切りマーカー付きで切り詰められる(self):
        message = "a" * TRUNCATION_BOUNDARY_LENGTH + "X"
        with patch("slack_notifier.urllib.request.urlopen") as urlopen:
            sn.post_to_slack("https://hooks.slack.com/x", message)
        sent = json.loads(urlopen.call_args[0][0].data.decode())["text"]
        assert "X" not in sent
        assert sent.endswith("\n…(truncated)")

    def test_送信が失敗したとき呼び出し元プロセスは終了コード1で終了する(self):
        with patch("slack_notifier.urllib.request.urlopen", side_effect=Exception("DUMMY-FAILURE-CAUSE-XYZ")):
            with pytest.raises(SystemExit) as exc:
                sn.post_to_slack("https://hooks.slack.com/x", "hello")
        assert exc.value.code == 1

    def test_送信が失敗したとき標準エラー出力に固定の案内文が出力される(self, capsys):
        with patch("slack_notifier.urllib.request.urlopen", side_effect=Exception("DUMMY-FAILURE-CAUSE-XYZ")):
            with pytest.raises(SystemExit):
                sn.post_to_slack("https://hooks.slack.com/x", "hello")
        assert "Slack notification failed" in capsys.readouterr().err

    def test_送信が失敗したとき送信を失敗させた原因の内容が標準エラー出力にそのまま含まれる(self, capsys):
        dummy_cause = "DUMMY-FAILURE-CAUSE-XYZ"
        with patch("slack_notifier.urllib.request.urlopen", side_effect=Exception(dummy_cause)):
            with pytest.raises(SystemExit):
                sn.post_to_slack("https://hooks.slack.com/x", "hello")
        assert dummy_cause in capsys.readouterr().err


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
