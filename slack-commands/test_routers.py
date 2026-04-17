#!/usr/bin/env python3
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from routers import db_control, gke_control, open_issues
from adapters.github import Issue, OpenIssuesResult


def run(coro):
    return asyncio.run(coro)


class TestParseEnv:
    """Slack コマンド引数から環境名を取り出す仕様。"""

    def test_strips_and_lowercases(self):
        """観点: 前後空白を取り除き小文字化して環境名に正規化する。"""
        assert db_control._parse_env("  DEV  ") == "dev"
        assert db_control._parse_env("STG\n") == "stg"
        assert gke_control._parse_env("  DEV  ") == "dev"

    def test_empty_returns_none(self):
        """観点: 空文字列・空白のみは None を返し、呼び出し側で使い方案内を出すトリガーになる。"""
        assert db_control._parse_env("") is None
        assert db_control._parse_env("   ") is None
        assert gke_control._parse_env("") is None


class TestDbControlHandle:
    """/db-start, /db-stop の分岐仕様。

    オーナー原則に従い本ルーターは workflow_dispatch のみ行い、
    Cloud SQL の実処理は overload-party-infra に委譲する。
    ユーザー体験として以下の仕様を固定する:
      - 環境未指定 → 使い方案内
      - 未対応環境 → エラー表示
      - dispatch 成功 → 🚀 ディスパッチ通知（完了通知はワークフロー側）
      - dispatch 失敗 → ⚠️ + エラー詳細を Slack に流して人間に判断を委ねる
    """

    @pytest.fixture(autouse=True)
    def _patches(self):
        with patch("routers.db_control.post_in_channel", new=AsyncMock()) as post, \
             patch("routers.db_control.dispatch_workflow", new=AsyncMock(return_value=None)) as dispatch:
            self.post = post
            self.dispatch = dispatch
            yield

    def test_missing_env_shows_usage(self):
        """観点: 環境未指定では dispatch せず、使い方案内を返す。"""
        run(db_control.handle_start("https://slack/response_url", ""))
        self.dispatch.assert_not_called()
        msg = self.post.call_args.args[1]
        assert "環境を指定してください" in msg
        assert "/db-start dev" in msg

    def test_unknown_env_shows_error(self):
        """観点: dev/stg 以外は dispatch せず、エラー表示。

        prod を誤って指定した時に本番 DB に何もしないことを保証する仕様。
        """
        run(db_control.handle_stop("https://slack/response_url", "prod"))
        self.dispatch.assert_not_called()
        msg = self.post.call_args.args[1]
        assert "未対応の環境" in msg
        assert "prod" in msg

    def test_start_dispatches_with_action_up(self):
        """観点: /db-start は action=up で overload-party-infra の workflow を dispatch する。"""
        run(db_control.handle_start("https://slack/response_url", "dev"))
        self.dispatch.assert_called_once()
        args = self.dispatch.call_args.args
        inputs = args[3]
        assert inputs["action"] == "up"
        assert inputs["environment"] == "dev"

    def test_stop_dispatches_with_action_down(self):
        """観点: /db-stop は action=down で dispatch する。"""
        run(db_control.handle_stop("https://slack/response_url", "stg"))
        self.dispatch.assert_called_once()
        inputs = self.dispatch.call_args.args[3]
        assert inputs["action"] == "down"
        assert inputs["environment"] == "stg"

    def test_dispatch_success_notifies_rocket(self):
        """観点: dispatch 成功時は「ディスパッチしました」を Slack に返す。"""
        run(db_control.handle_start("https://slack/response_url", "dev"))
        # post_in_channel は 1 回だけ呼ばれる（エラー時は異なるメッセージ）
        assert self.post.call_count == 1
        msg = self.post.call_args.args[1]
        assert ":rocket:" in msg
        assert "起動ワークフローをディスパッチしました" in msg

    def test_dispatch_failure_surfaces_error(self):
        """観点: dispatch 失敗時はエラー詳細を Slack に載せる（silent fail しない）。

        GitHub API の permission 不足等を原因追跡可能にするため、
        エラー内容をそのまま Slack に流す仕様。
        """
        self.dispatch.return_value = "GitHub API HTTP 403: ..."
        run(db_control.handle_start("https://slack/response_url", "dev"))
        msg = self.post.call_args.args[1]
        assert ":warning:" in msg
        assert "GitHub API HTTP 403" in msg

    def test_unexpected_exception_is_caught_and_notified(self):
        """観点: 予期しない例外でも Slack 通知を出してから抜ける。

        例外を投げたまま終わると Slack に何も出ず「反応なし」になるため、
        必ずユーザー向け通知を出す仕様。
        """
        self.dispatch.side_effect = RuntimeError("boom")
        run(db_control.handle_start("https://slack/response_url", "dev"))
        msg = self.post.call_args.args[1]
        assert ":warning:" in msg
        assert "ログを確認" in msg


class TestGkeControlHandle:
    """/gke-up, /gke-down の分岐仕様（db_control と対称）。"""

    @pytest.fixture(autouse=True)
    def _patches(self):
        with patch("routers.gke_control.post_in_channel", new=AsyncMock()) as post, \
             patch("routers.gke_control.dispatch_workflow", new=AsyncMock(return_value=None)) as dispatch:
            self.post = post
            self.dispatch = dispatch
            yield

    def test_up_missing_env_shows_usage(self):
        """観点: 環境未指定では dispatch せず使い方案内。"""
        run(gke_control.handle_up("https://slack/response_url", ""))
        self.dispatch.assert_not_called()
        assert "/gke-up dev" in self.post.call_args.args[1]

    def test_up_unknown_env_rejected(self):
        """観点: prod 等の未対応環境は dispatch せず拒否する。"""
        run(gke_control.handle_up("https://slack/response_url", "prod"))
        self.dispatch.assert_not_called()
        assert "未対応の環境" in self.post.call_args.args[1]

    def test_up_dispatches_with_action_up(self):
        """観点: /gke-up は action=up で overload-party-k8s の env-lifecycle に dispatch。"""
        run(gke_control.handle_up("https://slack/response_url", "dev"))
        inputs = self.dispatch.call_args.args[3]
        assert inputs["action"] == "up"
        assert inputs["environment"] == "dev"

    def test_down_dispatches_with_action_down(self):
        """観点: /gke-down は action=down で dispatch。"""
        run(gke_control.handle_down("https://slack/response_url", "stg"))
        inputs = self.dispatch.call_args.args[3]
        assert inputs["action"] == "down"

    def test_dispatch_failure_surfaces_error(self):
        """観点: GKE 側 dispatch 失敗もエラー詳細を Slack に載せる。"""
        self.dispatch.return_value = "GitHub API HTTP 500"
        run(gke_control.handle_up("https://slack/response_url", "dev"))
        msg = self.post.call_args.args[1]
        assert ":warning:" in msg
        assert "GitHub API HTTP 500" in msg


class TestOpenIssuesFormatResponse:
    """/open-issues のメッセージフォーマット仕様。

    4 分岐の出し分け:
      - 全件成功 + Issue あり → 件数サマリ + リポ別リスト
      - 全件成功 + Issue なし → ":white_check_mark: 未クローズの Issue はありません"
      - 一部失敗 + Issue なし → 失敗一覧のみ（誤った "Issue なし" を出さない）
      - 一部失敗 + Issue あり → 失敗一覧 + Issue 一覧
    """

    def test_clean_response(self):
        """観点: 成功かつ Issue が 0 件なら明示的に「Issue なし」を出す。"""
        result = OpenIssuesResult(issues_by_repo={}, failed_repos={})
        msg = open_issues._format_response(result)
        assert ":white_check_mark:" in msg
        assert "未クローズの Issue はありません" in msg

    def test_issues_listed_per_repo(self):
        """観点: Issue ありのケースで全件数と各リポの Issue が URL リンク付きで並ぶ。"""
        issues = {
            "repo-a": [Issue(number=1, title="bug 1", html_url="https://x/1")],
            "repo-b": [
                Issue(number=2, title="bug 2", html_url="https://x/2"),
                Issue(number=3, title="bug 3", html_url="https://x/3"),
            ],
        }
        result = OpenIssuesResult(issues_by_repo=issues, failed_repos={})
        msg = open_issues._format_response(result)
        # 全件数サマリ (1 + 2 = 3)
        assert "全 3 件" in msg
        assert "*repo-a* (1 件)" in msg
        assert "*repo-b* (2 件)" in msg
        # Slack link format <url|title>
        assert "<https://x/1|bug 1>" in msg
        assert "<https://x/3|bug 3>" in msg

    def test_failed_repos_are_shown(self):
        """観点: 取得失敗が 1 件でもあれば警告セクションに全部載せる。

        失敗の silent skip は「Issue が本当にないのか、取得できなかったのか」
        の区別を奪うため、全て明示する仕様。
        """
        result = OpenIssuesResult(
            issues_by_repo={},
            failed_repos={"repo-x": "404", "repo-y": "timeout"},
        )
        msg = open_issues._format_response(result)
        assert ":warning:" in msg
        assert "2 リポジトリで取得に失敗" in msg
        assert "repo-x" in msg and "404" in msg
        assert "repo-y" in msg and "timeout" in msg

    def test_partial_failure_does_not_claim_no_issues(self):
        """観点: 取得失敗があるのに "Issue はありません" を出さない。

        取得に失敗したリポがあるのに「Issue なし」と表示すると
        「取得できていないだけ」なのに「クリーン」と誤解させる。
        """
        result = OpenIssuesResult(
            issues_by_repo={},
            failed_repos={"repo-x": "403"},
        )
        msg = open_issues._format_response(result)
        assert "Issue はありません" not in msg
        assert ":warning:" in msg

    def test_both_failures_and_issues(self):
        """観点: 失敗と Issue がそれぞれある時は両セクションを並べて出す。"""
        issues = {"repo-a": [Issue(number=1, title="t", html_url="https://x/1")]}
        result = OpenIssuesResult(
            issues_by_repo=issues,
            failed_repos={"repo-x": "403"},
        )
        msg = open_issues._format_response(result)
        assert ":warning:" in msg
        assert "repo-x" in msg
        assert "*repo-a*" in msg
        assert "<https://x/1|t>" in msg
