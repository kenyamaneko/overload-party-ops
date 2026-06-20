"""cross-repo-seeds/slack_notifier.py のユニットテスト."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import slack_notifier as sn


class TestBuildActionsRunUrl:
    """GitHub Actions run URL 組み立ての仕様."""

    def test_builds_url_when_all_env_present(self):
        env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_RUN_ID": "42",
        }
        with patch.dict(os.environ, env, clear=True):
            assert sn.build_actions_run_url() == "https://github.com/org/repo/actions/runs/42"

    def test_returns_empty_when_env_missing(self):
        """観点: GITHUB_* が揃わないローカル実行では空文字を返す."""
        with patch.dict(os.environ, {}, clear=True):
            assert sn.build_actions_run_url() == ""

    def test_strips_trailing_slash_from_server_url(self):
        env = {
            "GITHUB_SERVER_URL": "https://github.com/",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_RUN_ID": "7",
        }
        with patch.dict(os.environ, env, clear=True):
            assert sn.build_actions_run_url() == "https://github.com/org/repo/actions/runs/7"


class TestPostToSlack:
    """Webhook 送信の仕様."""

    def test_sends_json_text_payload_to_webhook(self):
        with patch("slack_notifier.urllib.request.urlopen") as urlopen:
            sn.post_to_slack("https://hooks.slack.com/x", "hello")
        req = urlopen.call_args[0][0]
        assert req.full_url == "https://hooks.slack.com/x"
        assert json.loads(req.data.decode()) == {"text": "hello"}

    def test_truncates_message_over_limit(self):
        """観点: SLACK_TEXT_LIMIT 超過分は末尾を切り詰めて送る."""
        long_msg = "a" * (sn.SLACK_TEXT_LIMIT + 100)
        with patch("slack_notifier.urllib.request.urlopen") as urlopen:
            sn.post_to_slack("https://hooks.slack.com/x", long_msg)
        sent = json.loads(urlopen.call_args[0][0].data.decode())["text"]
        assert sent.endswith("\n…(truncated)")
        assert len(sent) == sn.SLACK_TEXT_LIMIT + len("\n…(truncated)")


class TestNotifyIfConfigured:
    """SLACK_WEBHOOK_URL の有無による送信ゲートの仕様."""

    def test_posts_when_webhook_configured(self):
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/x"}, clear=True), \
             patch("slack_notifier.post_to_slack") as post:
            sn.notify_if_configured("msg")
        post.assert_called_once_with("https://hooks.slack.com/x", "msg")

    def test_skips_when_webhook_unset(self, capsys):
        """観点: webhook 未設定なら送信せず stderr に記録のみ (ローカル実行を許容)."""
        with patch.dict(os.environ, {}, clear=True), patch("slack_notifier.post_to_slack") as post:
            sn.notify_if_configured("msg")
        post.assert_not_called()
        assert "SLACK_WEBHOOK_URL is not set" in capsys.readouterr().err
