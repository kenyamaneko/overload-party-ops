#!/usr/bin/env python3
import os
from unittest.mock import MagicMock, patch

import pytest
from review import (
    MAX_PROMPT_CHARS,
    ClaudeError,
    FileContentFetchError,
    GhError,
    IssueCreateError,
    PromptTooLargeError,
    _actions_run_url,
    _format_cmd_failure,
    _log_reference,
    get_diff,
    get_file_contents,
    is_no_issues,
    load_review_criteria,
    main,
    notify_slack,
    review_diff,
    review_repo,
    run_claude,
    truncate_diff,
)


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestTruncateDiff:
    def test_under_limit_returns_as_is(self):
        """観点: limit 以下なら無加工で返す（早期 return パス）。"""
        diff = "=== a.py ===\n+ hello\n"
        kept, omitted = truncate_diff(diff, limit=1000)
        assert kept == diff
        assert omitted == []

    def test_empty_string(self):
        """観点: 空入力でも例外を投げず空タプルを返す境界条件。"""
        kept, omitted = truncate_diff("", limit=100)
        assert kept == ""
        assert omitted == []

    def test_single_file_over_limit_is_kept(self):
        """観点: 1 ファイルだけで limit を超えるケースで全消失を避ける。

        kept が空のまま omitted に積むと「何もレビューできない」状態になるため、
        最初の 1 ファイルは limit 超過でも保持する仕様の固定。
        """
        diff = "=== big.py ===\n" + ("x" * 5000)
        kept, omitted = truncate_diff(diff, limit=100)
        assert kept == diff
        assert omitted == []

    def test_multiple_files_truncates_at_boundary(self):
        """観点: 複数ファイルの典型ケースで境界を超えた分が omitted に積まれる。"""
        section_a = "=== a.py ===\n" + ("a" * 50) + "\n"
        section_b = "=== b.py ===\n" + ("b" * 50) + "\n"
        section_c = "=== c.py ===\n" + ("c" * 50) + "\n"
        diff = section_a + section_b + section_c
        kept, omitted = truncate_diff(diff, limit=len(section_a) + 10)
        assert "a.py" in kept
        assert "b.py" not in kept
        assert "c.py" not in kept
        assert omitted == ["b.py", "c.py"]

    def test_truncation_keeps_file_boundaries(self):
        """観点: ファイル単位で切れること（行や文字の途中で切れない）。

        途中で切れた diff を Claude に渡すとレビュー精度が落ちるため、
        ファイル境界を保つことが仕様。
        """
        section_a = "=== a.py ===\n+ line1\n+ line2\n"
        section_b = "=== b.py ===\n+ line3\n"
        diff = section_a + section_b
        kept, omitted = truncate_diff(diff, limit=len(section_a))
        assert kept == section_a
        assert omitted == ["b.py"]

    def test_omitted_filename_is_extracted_from_header(self):
        """観点: 省略されたファイル名がヘッダ行から正しく抽出されて omitted に入る。

        omitted リストは Slack 通知のメッセージに使われるため、
        ファイル名（"b.py"）が "(unknown)" や None ではなく実名で出ることが必要。
        """
        diff = "=== a.py ===\nsmall\n=== b.py ===\n" + ("x" * 1000)
        kept, omitted = truncate_diff(diff, limit=30)
        assert "a.py" in kept
        assert omitted == ["b.py"]


class TestIsNoIssues:
    def test_exact_lgtm(self):
        """観点: 仕様の中心ケース（"LGTM" 完全一致）が True になること。"""
        assert is_no_issues("LGTM") is True

    def test_lgtm_with_surrounding_whitespace(self):
        """観点: 関数単体の防御契約として strip() してから比較する。

        実運用では run_claude が既に strip 済みの値を返すためここまでは届かないが、
        is_no_issues を別の経路から呼ばれた時に誤判定しないための関数契約。
        """
        assert is_no_issues("  LGTM  ") is True
        assert is_no_issues("\nLGTM\n") is True

    def test_lgtm_with_exclamation_is_not_lgtm(self):
        """観点: 厳密一致仕様の固定。"LGTM!" は指摘ありとして扱う。

        将来「LGTM! でも OK にする？」と迷った時に仕様を思い出すための回帰テスト。
        """
        assert is_no_issues("LGTM!") is False

    def test_lowercase_is_not_lgtm(self):
        """観点: 大文字小文字を区別する（"lgtm" は不一致）仕様の固定。"""
        assert is_no_issues("lgtm") is False

    def test_lgtm_with_extra_text_is_not_lgtm(self):
        """観点: LGTM の後に本文が続けば指摘ありとして Issue を作る。"""
        assert is_no_issues("LGTM\n問題なし") is False

    def test_empty_string_is_not_lgtm(self):
        """観点: 空文字列は LGTM ではなく指摘ありとして扱う境界条件。"""
        assert is_no_issues("") is False

    def test_review_body_is_not_lgtm(self):
        """観点: 通常のレビュー本文（指摘リスト）は当然 False。"""
        assert is_no_issues("- 設計に問題あり\n- バグ") is False


class TestFormatCmdFailure:
    def test_both_empty(self):
        """観点: 両方空の場合に「ともに空」のプレースホルダを返す。

        空文字をそのまま返すとログで原因が完全に消えるため、
        必ず何か出力する仕様の固定。
        """
        assert _format_cmd_failure("", "") == "(stderr/stdout ともに空)"

    def test_both_none(self):
        """観点: subprocess の戻り値が None でも例外を出さず空扱いにする。"""
        assert _format_cmd_failure(None, None) == "(stderr/stdout ともに空)"

    def test_only_whitespace(self):
        """観点: 空白のみは strip 後に空とみなす（実質的に情報なし）。"""
        assert _format_cmd_failure("   ", "\n\n") == "(stderr/stdout ともに空)"

    def test_stderr_only(self):
        """観点: 標準的な CLI（stderr にエラーを吐く）パターン。"""
        assert _format_cmd_failure("err msg", "") == "stderr: err msg"

    def test_stdout_only(self):
        """観点: Claude CLI のように stdout にしかエラーを吐かない CLI 対応。

        この関数の存在理由そのもの。stderr だけ見る実装にすると
        Claude CLI の失敗原因がログに出なくなる。
        """
        assert _format_cmd_failure("", "out msg") == "stdout: out msg"

    def test_both_present(self):
        """観点: 両方に出力がある場合は両方残す（情報を欠落させない）。"""
        result = _format_cmd_failure("err msg", "out msg")
        assert result == "stderr: err msg\nstdout: out msg"

    def test_strips_surrounding_whitespace(self):
        """観点: 出力前に strip され、不要な空白がメッセージに混入しない。"""
        assert _format_cmd_failure("  err\n", "  out\n") == "stderr: err\nstdout: out"


class TestGetDiff:
    """get_diff は以下の異常を silent に握りつぶさず全てエラー化する仕様。

    レビュー対象からファイルが silent に消えるのを避けるため:
      - commit_count が非整数（gh -q length の仕様変更検知）
      - compare API 行の JSON パース失敗
      - compare API レスポンスに必須フィールドが無い
    """

    def test_no_commits_returns_empty(self):
        """観点: コミット 0 件なら (None, []) を返し、正常に「差分なし」として扱われる。"""
        with patch("gh_client.gh", return_value="0"):
            diff, files = get_diff("repo-x", "main", "2026-04-16T00:00:00Z")
        assert diff is None
        assert files == []

    def test_non_digit_commit_count_raises(self):
        """観点: gh -q length が非整数を返したらエラー化（gh CLI 仕様変更の早期検知）。

        非数値を 0 コミット扱いに倒すと「API 応答が想定外」状態を隠蔽するため、
        silent に「差分なし」に倒さず例外で止める仕様。
        """
        with patch("gh_client.gh", return_value="abc"):
            with pytest.raises(GhError, match="整数ではありません"):
                get_diff("repo-x", "main", "2026-04-16T00:00:00Z")

    def test_malformed_json_line_raises(self):
        """観点: compare API 行の JSON パース失敗は silent skip せず GhError を投げる。

        パース失敗を continue で飛ばすと該当ファイルだけ無言でレビュー対象から
        消え、レビュー品質が silent に劣化するため例外で止める仕様。
        """
        # 1 行目は valid、2 行目が壊れた JSON
        valid = '{"filename": "a.py", "status": "modified", "patch": "+x"}'
        invalid = "not-json-at-all"
        gh_outputs = ["2", f"{valid}\n{invalid}"]
        with patch("gh_client.gh", side_effect=gh_outputs):
            with pytest.raises(GhError, match="JSON パース失敗"):
                get_diff("repo-x", "main", "2026-04-16T00:00:00Z")

    def test_missing_field_raises(self):
        """観点: compare API レスポンスから必須フィールド (filename/status/patch) が欠けたらエラー。

        KeyError を continue で握ると該当ファイルが silent にレビュー対象から落ちるため例外で止める。
        """
        # patch フィールドが欠けている
        bad = '{"filename": "a.py", "status": "modified"}'
        gh_outputs = ["1", bad]
        with patch("gh_client.gh", side_effect=gh_outputs):
            with pytest.raises(GhError, match="必須フィールド"):
                get_diff("repo-x", "main", "2026-04-16T00:00:00Z")

    def test_valid_response_returns_parsed_diff(self):
        """観点: 正常レスポンスで diff とファイル情報が組み立てられる。"""
        entry = '{"filename": "a.py", "status": "modified", "patch": "+added line"}'
        gh_outputs = ["1", entry]
        with patch("gh_client.gh", side_effect=gh_outputs):
            diff, files = get_diff("repo-x", "main", "2026-04-16T00:00:00Z")
        assert diff is not None
        assert "=== a.py ===" in diff
        assert "+added line" in diff
        assert files == [{"filename": "a.py", "status": "modified"}]


class TestReviewDiff:
    """review_diff のプロンプト組み立てロジックを検証。get_diff/get_file_contents/run_claude のみモック。"""

    @pytest.fixture(autouse=True)
    def _patches(self):
        with patch("review.get_diff") as get_diff, \
             patch("review.get_file_contents", return_value="") as get_file_contents, \
             patch("review.run_claude", return_value="LGTM") as run_claude:
            self.get_diff = get_diff
            self.get_file_contents = get_file_contents
            self.run_claude = run_claude
            yield

    def test_no_diff_returns_none_without_calling_claude(self):
        """観点: 差分なしなら Claude を呼ばずに None を返す（コスト削減）。"""
        self.get_diff.return_value = (None, [])
        result = review_diff("repo-x", "main", "2026-04-16")
        assert result is None
        self.run_claude.assert_not_called()

    def test_truncated_diff_includes_omitted_filenames_in_note(self):
        """観点: diff が切り詰められた時、省略ファイル名が note としてプロンプトに入る。

        Claude に「省略がある」事実を伝えないとレビュー結果がミスリードになるため、
        note の存在と中身（省略ファイル名）が将来のリグレッションで失われないよう固定。
        """
        # MAX_DIFF_CHARS を超える複数ファイル diff を作る
        big_section = "=== big.py ===\n" + ("x" * 200000) + "\n"
        small_section = "=== small.py ===\n+ y\n"
        diff = big_section + small_section + small_section.replace("small.py", "tiny.py")
        # 全部入れると 300000 chars 超え → tiny.py だけ omitted になる想定
        self.get_diff.return_value = (diff * 2, [{"filename": "x", "status": "modified"}])
        review_diff("repo-x", "main", "2026-04-16")
        # run_claude に渡された prompt を取り出す
        prompt = self.run_claude.call_args.args[0]
        assert "省略" in prompt
        # 何かしら omitted ファイル名が note に入っていること
        assert "（注：差分が大きいため以下のファイルは省略されています:" in prompt

    def test_no_truncation_no_note(self):
        """観点: 切り詰めが無い時はプロンプトに省略 note を入れない（ノイズ抑制）。"""
        self.get_diff.return_value = (
            "=== a.py ===\n+ x\n",
            [{"filename": "a.py", "status": "modified"}],
        )
        review_diff("repo-x", "main", "2026-04-16")
        prompt = self.run_claude.call_args.args[0]
        assert "省略" not in prompt

    def test_file_context_appended_when_files_present(self):
        """観点: ファイル全文取得結果がプロンプトに「全文セクション」として付加される。"""
        self.get_diff.return_value = (
            "=== a.py ===\n+ x\n",
            [{"filename": "a.py", "status": "modified"}],
        )
        self.get_file_contents.return_value = "=== a.py (full) ===\nfull body"
        review_diff("repo-x", "main", "2026-04-16")
        prompt = self.run_claude.call_args.args[0]
        assert "以下は変更されたファイルの全文です" in prompt
        assert "full body" in prompt

    def test_no_file_context_section_when_get_file_contents_empty(self):
        """観点: get_file_contents が空文字列を返した時は全文セクションを付けない。

        空のコードブロックがプロンプトに混入してトークンを浪費するのを防ぐ。
        """
        self.get_diff.return_value = (
            "=== a.py ===\n+ x\n",
            [{"filename": "a.py", "status": "modified"}],
        )
        self.get_file_contents.return_value = ""
        review_diff("repo-x", "main", "2026-04-16")
        prompt = self.run_claude.call_args.args[0]
        assert "以下は変更されたファイルの全文です" not in prompt

    def test_prompt_too_large_raises(self):
        """観点: diff + file_context + ヘッダの合計が MAX_PROMPT_CHARS を超えたら例外。

        この上限が外れると Claude CLI 側で context overflow を起こし、
        失敗詳細が握りつぶされるため、Python 側で先に防ぐ仕様の固定。
        """
        self.get_diff.return_value = (
            "=== a.py ===\n+ x\n",
            [{"filename": "a.py", "status": "modified"}],
        )
        # MAX_PROMPT_CHARS を確実に超えるサイズ
        self.get_file_contents.return_value = "x" * (MAX_PROMPT_CHARS + 100)
        with pytest.raises(PromptTooLargeError):
            review_diff("repo-x", "main", "2026-04-16")
        self.run_claude.assert_not_called()


class TestReviewRepo:
    """review_repo の分岐ロジックを検証。AI 応答や外部 I/O はモック。"""

    @pytest.fixture(autouse=True)
    def _patches(self):
        # 外部 I/O とラッパーは全部モック。review_repo の分岐だけを見たい。
        with patch("review.notify_slack") as notify, \
             patch("review.ensure_label") as ensure_label, \
             patch("review.issue_exists", return_value=False) as issue_exists, \
             patch("review.create_issue", return_value="https://example/issue/1") as create_issue, \
             patch("review.review_diff") as review_diff:
            self.notify = notify
            self.ensure_label = ensure_label
            self.issue_exists = issue_exists
            self.create_issue = create_issue
            self.review_diff = review_diff
            yield

    def _call(self, **overrides):
        defaults = {
            "entry": {"name": "repo-x", "branch": "main"},
            "today": "2026-04-17",
            "yesterday": "2026-04-16",
            "skip_if_exists": True,
            "label": "auto-review",
        }
        defaults.update(overrides)
        return review_repo(**defaults)

    def test_branch_unset_notifies_and_returns_error(self):
        """観点: 設定不備（branch 未設定）は早期 return + Slack 通知 + エラー扱い。

        設定ミスを silent fail させない。レビュー処理にも入らない。
        """
        result = self._call(entry={"name": "repo-x"})
        assert result is True
        assert self.notify.call_count == 1
        self.review_diff.assert_not_called()
        self.create_issue.assert_not_called()

    def test_skip_if_existing_issue(self):
        """観点: 既存 Issue がある日は冪等にスキップ（重複作成を防ぐ）。

        通知も飛ばないこと（毎日 Slack に通知が再送されると困る）。
        """
        self.issue_exists.return_value = True
        result = self._call()
        assert result is False
        self.review_diff.assert_not_called()
        self.create_issue.assert_not_called()
        self.notify.assert_not_called()

    def test_no_diff_returns_normally(self):
        """観点: 差分なし（review_diff が None）は正常終了で何も通知しない。"""
        self.review_diff.return_value = None
        result = self._call()
        assert result is False
        self.create_issue.assert_not_called()
        self.notify.assert_not_called()

    def test_lgtm_skips_issue_creation(self):
        """観点: LGTM 応答を受けたら Issue も作らず通知もしない（ノイズ抑制）。"""
        self.review_diff.return_value = "LGTM"
        result = self._call()
        assert result is False
        self.create_issue.assert_not_called()
        self.notify.assert_not_called()

    def test_review_body_creates_issue_and_notifies(self):
        """観点: 指摘ありの正常系で Issue が 1 回作られ、通知に title と URL が含まれる。"""
        self.review_diff.return_value = "- 指摘1\n- 指摘2"
        result = self._call()
        assert result is False
        self.create_issue.assert_called_once()
        assert self.notify.call_count == 1
        title_arg, body_arg = self.notify.call_args.args
        assert "[自動レビュー" in title_arg
        assert "repo-x" in title_arg
        assert "https://example/issue/1" in body_arg

    def test_prompt_too_large_notifies_and_returns_error(self):
        """観点: プロンプト超過は Slack 通知 + エラー扱い、Issue は作らない。

        握りつぶしや空 Issue 作成を防ぐ仕様の固定。
        """
        self.review_diff.side_effect = PromptTooLargeError("too big")
        result = self._call()
        assert result is True
        assert self.notify.call_count == 1
        self.create_issue.assert_not_called()

    def test_claude_error_notifies_and_returns_error(self):
        """観点: Claude CLI 失敗時に Slack 通知 + エラー扱い、Issue は作らない。"""
        self.review_diff.side_effect = ClaudeError("boom")
        result = self._call()
        assert result is True
        assert self.notify.call_count == 1
        self.create_issue.assert_not_called()

    def test_file_content_fetch_error_notifies_and_returns_error(self):
        """観点: ファイル全文取得失敗を握りつぶさず Slack に必ず流す。

        無言でレビュー品質が劣化するのを防ぐための明示的な通知ポリシー。
        """
        self.review_diff.side_effect = FileContentFetchError("fetch failed")
        result = self._call()
        assert result is True
        assert self.notify.call_count == 1
        self.create_issue.assert_not_called()

    def test_gh_error_notifies_and_returns_error(self):
        """観点: GitHub API 失敗時の通知 + エラー扱い。"""
        self.review_diff.side_effect = GhError("gh failed")
        result = self._call()
        assert result is True
        assert self.notify.call_count == 1
        self.create_issue.assert_not_called()

    def test_issue_create_error_notifies_and_returns_error(self):
        """観点: Issue 作成失敗時もレビュー結果を消さず Slack に通知する。

        レビュー本文があるのに Issue 作成だけ失敗、というケースで
        結果が完全に消失するのを防ぐ。
        """
        self.review_diff.return_value = "- 指摘あり"
        self.create_issue.side_effect = IssueCreateError("create failed")
        result = self._call()
        assert result is True
        assert self.notify.call_count == 1


class TestRunClaude:
    """Claude CLI 呼び出し。stdout にしかエラーを吐く Claude CLI に対応するのが要所。

    exit 0 でも stdout が空なら None を返し、呼び出し側で「応答なし」扱いに倒す。
    これを silent に "" で返すと is_no_issues("") = False になり、空の Issue が
    自動作成される silent failure になる。
    """

    def test_success_returns_stripped_stdout(self):
        """観点: exit 0 + stdout あり → strip して返す。"""
        with patch("review.subprocess.run", return_value=_proc(0, stdout="  LGTM  \n")):
            assert run_claude("prompt") == "LGTM"

    def test_empty_stdout_returns_none(self):
        """観点: exit 0 でも stdout が空なら None を返す。

        空文字を返すと Issue 本文が空の状態で作成される silent failure を生む。
        必ず None で「応答なし」を明示する仕様の固定。
        """
        with patch("review.subprocess.run", return_value=_proc(0, stdout="")):
            assert run_claude("prompt") is None

    def test_whitespace_only_stdout_returns_none(self):
        """観点: 空白のみの stdout も strip 後に空なので None 扱い。"""
        with patch("review.subprocess.run", return_value=_proc(0, stdout="   \n\n")):
            assert run_claude("prompt") is None

    def test_nonzero_exit_raises_claudeerror(self):
        """観点: exit != 0 なら ClaudeError を投げる（silent に None 扱いしない）。"""
        with patch("review.subprocess.run", return_value=_proc(1, stderr="boom")):
            with pytest.raises(ClaudeError, match="exit code 1"):
                run_claude("prompt")

    def test_error_detail_includes_stdout_when_stderr_empty(self):
        """観点: Claude CLI が stdout にしかエラーを吐く場合も ClaudeError に詳細が入る。

        この関数の存在理由。stderr だけ見る実装にすると Claude CLI の失敗原因が
        完全に消失する。_format_cmd_failure 経由で両方拾うことの結合テスト。
        """
        with patch("review.subprocess.run", return_value=_proc(1, stdout="error in stdout", stderr="")):
            with pytest.raises(ClaudeError, match="error in stdout"):
                run_claude("prompt")

    def test_429_retries_then_succeeds(self):
        """観点: 429 レート制限は指数バックオフで再試行し、途中で成功したら結果を返す。

        Rate limit は 1 分ウィンドウで自然解消する一時エラーのため、即 abort せず粘る仕様。
        初回 429 → sleep → 再試行で成功するパスが壊れないことを固定する。
        """
        rate_limit_stdout = "API Error: Request rejected (429) · rate limit"
        procs = [
            _proc(1, stdout=rate_limit_stdout),
            _proc(0, stdout="LGTM"),
        ]
        with patch("review.subprocess.run", side_effect=procs) as run, \
             patch("review.time.sleep") as sleep:
            assert run_claude("prompt") == "LGTM"
        assert run.call_count == 2
        assert sleep.call_count == 1

    def test_429_retries_exhausted_raises(self):
        """観点: MAX_429_RETRIES 回再試行しても 429 が続く場合は ClaudeError を投げる。

        無限リトライを防ぎつつ、握りつぶしもしない上限の固定。
        最後の試行でも 429 なら「本当にレート制限で失敗」として人間に通知する。
        """
        rate_limit_stdout = "API Error: Request rejected (429) · rate limit"
        # MAX_429_RETRIES + 1 回 = 初回 + 再試行回数 の全てで 429
        with patch("review.subprocess.run", return_value=_proc(1, stdout=rate_limit_stdout)) as run, \
             patch("review.time.sleep") as sleep:
            with pytest.raises(ClaudeError, match=r"\(429\)"):
                run_claude("prompt")
        # 初回 + MAX_429_RETRIES 回の再試行 = MAX_429_RETRIES + 1 回の実行
        assert run.call_count == 4
        # 最後の試行のあとは sleep しない（sleep するとしても再試行しないので無駄）
        assert sleep.call_count == 3

    def test_non_429_error_does_not_retry(self):
        """観点: 429 以外のエラーは即 raise し、再試行の sleep も発生しない。

        「待てば直る」性質の無いエラー（プロンプト不正・CLI バグ等）でリトライすると
        無駄に時間を消費するため、429 専用のリトライ分岐であることを固定する。
        """
        with patch("review.subprocess.run", return_value=_proc(1, stderr="some other error")) as run, \
             patch("review.time.sleep") as sleep:
            with pytest.raises(ClaudeError, match="some other error"):
                run_claude("prompt")
        assert run.call_count == 1
        sleep.assert_not_called()


class TestLoadReviewCriteria:
    """レビュー観点 YAML のロードと整形。構造異常は silent に通さず例外にする仕様を固定。

    空観点で Claude が LGTM を返すと「レビューなし」の silent fallback が起きるため、
    ファイル構造の異常は必ず例外で止める。
    """

    def test_formats_categories_as_markdown_sections(self, tmp_path):
        """観点: YAML が正常な場合、カテゴリ名が "## {name}" 見出し、観点が "- {item}" で展開される。

        この整形が崩れると Claude 側のレビュー品質に直結するため、
        出力フォーマットを仕様として固定する。
        """
        yaml_file = tmp_path / "criteria.yaml"
        yaml_file.write_text(
            "categories:\n"
            "  - name: 設計\n"
            "    items:\n"
            "      - 観点A\n"
            "      - 観点B\n"
            "  - name: テスト\n"
            "    items:\n"
            "      - 観点C\n"
        )
        with patch("review.REVIEW_CRITERIA_YAML", yaml_file):
            result = load_review_criteria()
        assert result.startswith("以下の観点でレビューしてください。")
        assert "## 設計" in result
        assert "- 観点A" in result
        assert "- 観点B" in result
        assert "## テスト" in result
        assert "- 観点C" in result

    def test_missing_categories_key_raises(self, tmp_path):
        """観点: categories キーが無い YAML はエラー化（silent に空観点で走らせない）。"""
        yaml_file = tmp_path / "criteria.yaml"
        yaml_file.write_text("other_key: value\n")
        with patch("review.REVIEW_CRITERIA_YAML", yaml_file):
            with pytest.raises(KeyError):
                load_review_criteria()

    def test_empty_categories_list_raises(self, tmp_path):
        """観点: categories が空リストの YAML はエラー化。

        空観点でのレビュー実行を防ぐ。silent に通すと Claude が LGTM を返して
        「指摘なし」扱いになり、レビューが無言で機能停止するのを防ぐ。
        """
        yaml_file = tmp_path / "criteria.yaml"
        yaml_file.write_text("categories: []\n")
        with patch("review.REVIEW_CRITERIA_YAML", yaml_file):
            with pytest.raises(ValueError, match="categories"):
                load_review_criteria()

    def test_empty_items_in_category_raises(self, tmp_path):
        """観点: カテゴリの items が空リストの場合もエラー化。

        カテゴリだけ書いて中身を書き忘れた場合に silent に通すと観点が歯抜けになるため、
        部分的な不整合もエラーで止める仕様の固定。
        """
        yaml_file = tmp_path / "criteria.yaml"
        yaml_file.write_text(
            "categories:\n"
            "  - name: 設計\n"
            "    items: []\n"
        )
        with patch("review.REVIEW_CRITERIA_YAML", yaml_file):
            with pytest.raises(ValueError, match="items"):
                load_review_criteria()

    def test_missing_name_or_items_key_raises(self, tmp_path):
        """観点: name / items キー自体が欠けている場合も KeyError で止める（silent skip しない）。"""
        yaml_file = tmp_path / "criteria.yaml"
        yaml_file.write_text(
            "categories:\n"
            "  - name: 設計\n"  # items キーが無い
        )
        with patch("review.REVIEW_CRITERIA_YAML", yaml_file):
            with pytest.raises(KeyError):
                load_review_criteria()


class TestGetFileContents:
    """変更ファイル全文取得。status=removed のスキップと error の非握りつぶしが要所。

    削除済みファイルは branch HEAD に存在せず `gh contents` が 404 を返すため、
    取得対象から事前に除外する仕様。除外が外れると FileContentFetchError → Slack 通知
    の経路に流れるが、意味的には「削除ファイルを取得しようとした設計バグ」なので、
    フィルタリングを仕様として固定する。
    """

    def test_skips_removed_files(self):
        """観点: status=removed のファイルは gh API を叩かず飛ばす。

        branch HEAD に存在しないため取得を試みると 404。silent fail するのではなく
        設計上の対象外として事前フィルタする仕様の固定。
        """
        files = [
            {"filename": "a.py", "status": "removed"},
            {"filename": "b.py", "status": "modified"},
        ]
        with patch("gh_client.gh", return_value="aGVsbG8=") as gh_mock:  # base64 "hello"
            result = get_file_contents("repo", "main", files, limit=10000)
        assert gh_mock.call_count == 1
        assert "b.py" in result
        assert "a.py" not in result

    def test_gh_error_wrapped_in_fetch_error(self):
        """観点: gh 失敗は FileContentFetchError に wrap される（Slack 通知経路に繋げる）。

        review_repo で FileContentFetchError の except 節が用意されているため、
        GhError を素通しせず wrap して呼び出し側の分岐を明確にする。
        """
        files = [{"filename": "a.py", "status": "modified"}]
        with patch("gh_client.gh", side_effect=GhError("api failed")):
            with pytest.raises(FileContentFetchError, match="a.py"):
                get_file_contents("repo", "main", files, limit=10000)

    def test_base64_decode_error_wrapped(self):
        """観点: base64 decode 失敗も FileContentFetchError に wrap。

        gh API が仕様変更で base64 以外を返した場合に silent skip せず、
        人間の調査を促すために例外に上げる。
        """
        files = [{"filename": "a.py", "status": "modified"}]
        # base64.b64decode は緩い（invalid 文字を stripping する）ため、
        # 決定的にエラーを出すには b64decode 自体をモックで failing にする
        with patch("gh_client.gh", return_value="anything"), \
             patch("gh_client.base64.b64decode", side_effect=ValueError("bad base64")):
            with pytest.raises(FileContentFetchError, match="a.py"):
                get_file_contents("repo", "main", files, limit=10000)

    def test_first_file_kept_even_over_limit(self):
        """観点: 1 ファイル目が limit 超過でも保持する（全消失の防止）。

        truncate_diff と同様、kept が空のまま進めるとレビュー品質が 0 になるため、
        最初の 1 ファイルは必ず保持。
        """
        files = [
            {"filename": "big.py", "status": "modified"},
            {"filename": "skipped.py", "status": "modified"},
        ]
        with patch("gh_client.gh", return_value="aGVsbG8="):
            result = get_file_contents("repo", "main", files, limit=10)
        assert "big.py" in result
        assert "skipped.py" not in result


class TestNotifySlackReview:
    """レビュー通知の失敗時挙動。通知失敗はログ残して継続（レビュー本体を止めない）が仕様。"""

    def test_no_webhook_url_silent_skip(self):
        """観点: SLACK_WEBHOOK_URL 未設定なら silent に抜ける。

        開発環境等で意図的に Webhook を設定しないケースがあるため、
        欠落を error ではなく正常系として扱う。
        """
        with patch.dict(os.environ, {}, clear=True):
            notify_slack("title", "body")  # 例外が出ないこと

    def test_urlopen_exception_does_not_raise(self, capsys):
        """観点: 通知失敗は warning ログのみで、例外は上げない（レビュー本体を止めない）。

        notify_slack が例外を raise すると review_repo の後続処理や他リポジトリの
        レビューに波及するため、通知失敗だけは warning に留める。ただしログは必ず残す。
        """
        env = {"SLACK_WEBHOOK_URL": "https://invalid.example/"}
        with patch.dict(os.environ, env, clear=True), \
             patch("review.urllib.request.urlopen", side_effect=Exception("unreachable")):
            notify_slack("title", "body")  # 例外が出ないこと
        captured = capsys.readouterr()
        assert "Warning" in captured.out
        assert "unreachable" in captured.out

    def test_non_200_status_logs_warning(self, capsys):
        """観点: Slack が non-200 を返した場合も warning ログに残す。

        silent に成功扱いすると、Slack 側の障害や webhook 期限切れに気付けない。
        """
        resp = MagicMock()
        resp.status = 500
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=resp)
        ctx.__exit__ = MagicMock(return_value=False)
        env = {"SLACK_WEBHOOK_URL": "https://slack/"}
        with patch.dict(os.environ, env, clear=True), \
             patch("review.urllib.request.urlopen", return_value=ctx):
            notify_slack("title", "body")
        captured = capsys.readouterr()
        assert "500" in captured.out


class TestActionsRunUrl:
    """Actions run URL の組み立て。環境変数の有無で出力を切り替える。"""

    def test_all_env_vars_present(self):
        """観点: 必要な 3 つの環境変数が揃った時に URL を組み立てる。"""
        env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_RUN_ID": "12345",
        }
        with patch.dict(os.environ, env, clear=True):
            assert _actions_run_url() == "https://github.com/org/repo/actions/runs/12345"

    def test_missing_run_id_returns_empty(self):
        """観点: RUN_ID が欠けていれば URL を組み立てない（ローカル実行など）。"""
        env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "org/repo",
        }
        with patch.dict(os.environ, env, clear=True):
            assert _actions_run_url() == ""

    def test_no_env_vars_returns_empty(self):
        """観点: 全て未設定なら空文字（URL 埋め込みはスキップ）。"""
        with patch.dict(os.environ, {}, clear=True):
            assert _actions_run_url() == ""

    def test_trailing_slash_stripped(self):
        """観点: GITHUB_SERVER_URL の末尾スラッシュでダブルスラッシュにならない。"""
        env = {
            "GITHUB_SERVER_URL": "https://github.com/",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_RUN_ID": "1",
        }
        with patch.dict(os.environ, env, clear=True):
            assert _actions_run_url() == "https://github.com/org/repo/actions/runs/1"


class TestLogReference:
    """Slack 本文に埋める「ログ所在」テキスト。URL 有無の両ケースを明示する仕様。

    silent に URL を省略すると「詳細はログ」と書いてもユーザーが実際の所在を
    探せない状態になるため、どちらのケースも明示的に表現する。
    """

    def test_url_available_returns_slack_link(self):
        """観点: Actions URL が取れる時は Slack リンク形式で返す。"""
        env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_RUN_ID": "1",
        }
        with patch.dict(os.environ, env, clear=True):
            ref = _log_reference()
        assert ref == "<https://github.com/org/repo/actions/runs/1|Actions ログ>"

    def test_cloud_run_job_env_returns_logging_link(self):
        """観点: Cloud Run Job の env が揃った時は Logging Explorer の Slack リンクを返す。

        nightly-review は Cloud Run Jobs で実行されており、Actions URL は取れない。
        この経路で URL を出さないと Slack 通知から「stdout どこで見るの？」になり
        前回の silent failure（placeholder image）の原因調査が遅れた経緯があるため、
        Cloud Run env (CLOUD_RUN_EXECUTION / CLOUD_RUN_JOB / GOOGLE_CLOUD_PROJECT)
        からログクエリ URL を組み立てる仕様を固定する。
        """
        env = {
            "CLOUD_RUN_EXECUTION": "nightly-review-79jgf",
            "CLOUD_RUN_JOB": "nightly-review",
            "GOOGLE_CLOUD_PROJECT": "overload-party-ops",
        }
        with patch.dict(os.environ, env, clear=True):
            ref = _log_reference()
        assert ref.startswith("<https://console.cloud.google.com/logs/query;query=")
        assert ref.endswith("|Cloud Logging>")
        assert "nightly-review-79jgf" in ref
        assert "overload-party-ops" in ref

    def test_actions_url_takes_precedence_over_cloud_run(self):
        """観点: 両環境の env が揃っていても Actions URL を優先する（実行元の優先順位を固定）。"""
        env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_RUN_ID": "1",
            "CLOUD_RUN_EXECUTION": "exec",
            "CLOUD_RUN_JOB": "job",
            "GOOGLE_CLOUD_PROJECT": "proj",
        }
        with patch.dict(os.environ, env, clear=True):
            ref = _log_reference()
        assert "actions/runs/1" in ref
        assert "console.cloud.google.com" not in ref

    def test_partial_cloud_run_env_falls_through_to_absence(self):
        """観点: Cloud Run env が部分的に欠ける場合は URL を作らず「取得不可」へ倒す。

        欠けた要素を空文字で埋めて壊れた URL を Slack に貼る silent な品質劣化を防ぐ。
        """
        env = {
            "CLOUD_RUN_EXECUTION": "exec",
            "CLOUD_RUN_JOB": "job",
            # GOOGLE_CLOUD_PROJECT を欠落させる
        }
        with patch.dict(os.environ, env, clear=True):
            ref = _log_reference()
        assert "取得不可" in ref

    def test_url_missing_indicates_absence_explicitly(self):
        """観点: どの実行環境も特定できない時、silent に省略せず取得不可を明示する。"""
        with patch.dict(os.environ, {}, clear=True):
            ref = _log_reference()
        assert "取得不可" in ref


class TestReviewRepoErrorNotificationsIncludeLogReference:
    """review_repo の「詳細はログ」系通知が実際にログへの動線を含むことを固定する。

    この統合テストが無いと「詳細はログ」と本文に書きながら実際の所在を載せていない
    silent 劣化が紛れ込む。_log_reference 単体テストだけでは呼び出し側が使っている
    ことの保証にならないため、notify_slack call の本文を直接検証する。
    """

    @pytest.fixture(autouse=True)
    def _patches(self):
        env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_RUN_ID": "999",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("review.notify_slack") as notify, \
             patch("review.ensure_label"), \
             patch("review.issue_exists", return_value=False), \
             patch("review.create_issue", return_value="https://example/issue/1") as create_issue, \
             patch("review.review_diff") as review_diff:
            self.notify = notify
            self.create_issue = create_issue
            self.review_diff = review_diff
            yield

    def _call(self):
        return review_repo(
            entry={"name": "repo-x", "branch": "main"},
            today="2026-04-17",
            yesterday="2026-04-16",
            skip_if_exists=True,
            label="auto-review",
        )

    def _notify_body(self) -> str:
        return self.notify.call_args.args[1]

    def test_claude_error_body_includes_log_url(self):
        """観点: ClaudeError 通知に Actions ログ URL が含まれる。"""
        self.review_diff.side_effect = ClaudeError("boom")
        self._call()
        body = self._notify_body()
        assert "actions/runs/999" in body
        assert "Claude CLI 失敗" in body

    def test_gh_error_body_includes_log_url(self):
        """観点: GhError 通知に Actions ログ URL が含まれる。"""
        self.review_diff.side_effect = GhError("gh failed")
        self._call()
        body = self._notify_body()
        assert "actions/runs/999" in body
        assert "GitHub API 失敗" in body

    def test_issue_create_error_body_includes_log_url(self):
        """観点: IssueCreateError 通知に Actions ログ URL が含まれる。"""
        self.review_diff.return_value = "- 指摘あり"
        self.create_issue.side_effect = IssueCreateError("create failed")
        self._call()
        body = self._notify_body()
        assert "actions/runs/999" in body
        assert "Issue 作成失敗" in body


class TestSlackTitleEmojiPrefix:
    """Slack 通知タイトルに種別マーカー絵文字が付いている仕様の固定。

    ops リポ全体で他の通知（cost-monitor / drift-monitor / slack-commands）が
    `:x:` `:warning:` `:rotating_light:` `:white_check_mark:` `:rocket:` で種別を
    視覚的に伝えており、Nightly Review だけ無印だと一覧で重要度が一致しなくなる。
    本クラスは「種別ごとに正しい絵文字が付く」ことを仕様として固定する。

    なお `_notify_title` は `notify_slack` 第 1 引数。本文ではなくタイトル側に
    絵文字を付けるのが既存規約。
    """

    @pytest.fixture(autouse=True)
    def _patches(self):
        with patch("review.notify_slack") as notify, \
             patch("review.ensure_label"), \
             patch("review.issue_exists", return_value=False), \
             patch("review.create_issue", return_value="https://example/issue/1") as create_issue, \
             patch("review.review_diff") as review_diff:
            self.notify = notify
            self.create_issue = create_issue
            self.review_diff = review_diff
            yield

    def _call(self, entry=None):
        return review_repo(
            entry=entry or {"name": "repo-x", "branch": "main"},
            today="2026-04-17",
            yesterday="2026-04-16",
            skip_if_exists=True,
            label="auto-review",
        )

    def _notify_title(self) -> str:
        return self.notify.call_args.args[0]

    def test_branch_unset_uses_x_marker(self):
        """観点: 設定エラー（branch 未設定）は :x: でエラーカテゴリとして表示。"""
        self._call(entry={"name": "repo-x"})
        assert self._notify_title().startswith(":x: ")

    def test_prompt_too_large_uses_warning_marker(self):
        """観点: スキップ系（PromptTooLarge）は :warning:。

        エラーではなく「自動処理をやめて人間判断に任せた」状態のため、
        :x: ではなく :warning: で区別する。
        """
        self.review_diff.side_effect = PromptTooLargeError("too big")
        self._call()
        assert self._notify_title().startswith(":warning: ")

    def test_claude_error_uses_x_marker(self):
        """観点: ClaudeError は :x:。"""
        self.review_diff.side_effect = ClaudeError("boom")
        self._call()
        assert self._notify_title().startswith(":x: ")

    def test_file_content_fetch_error_uses_x_marker(self):
        """観点: FileContentFetchError は :x:。"""
        self.review_diff.side_effect = FileContentFetchError("fetch failed")
        self._call()
        assert self._notify_title().startswith(":x: ")

    def test_gh_error_uses_x_marker(self):
        """観点: GhError は :x:。"""
        self.review_diff.side_effect = GhError("gh failed")
        self._call()
        assert self._notify_title().startswith(":x: ")

    def test_issue_create_error_uses_x_marker(self):
        """観点: IssueCreateError は :x:。"""
        self.review_diff.return_value = "- 指摘あり"
        self.create_issue.side_effect = IssueCreateError("create failed")
        self._call()
        assert self._notify_title().startswith(":x: ")

    def test_review_body_notify_uses_memo_marker(self):
        """観点: 指摘あり Issue 作成成功通知は :memo:（成功カテゴリだが「読むべき内容あり」を示す）。

        :white_check_mark: にしてしまうと「問題なし」と読み違えるため、
        Issue 作成済み（=指摘あり）を表す :memo: を使う仕様。
        """
        self.review_diff.return_value = "- 指摘1"
        self._call()
        assert self._notify_title().startswith(":memo: ")

    def test_unexpected_exception_inside_loop_uses_rotating_light_marker(self):
        """観点: リポジトリループ内で想定外例外が起きた時、:rotating_light: 付きで通知する。

        review_repo の except 群に該当しない例外は main() の外側 try で拾う設計で、
        この経路の絵文字付与が外れると「予期しないエラーだけ無印で目立たない」
        というリグレッションが起きるため固定する。
        """
        with patch("review.load_repos", return_value=[{"name": "repo-x", "branch": "main"}]), \
             patch("review.review_repo", side_effect=RuntimeError("unexpected boom")):
            with pytest.raises(SystemExit):
                main()
        assert self._notify_title().startswith(":rotating_light: ")
        assert "repo-x" in self._notify_title()

    def test_unexpected_exception_before_loop_uses_rotating_light_marker(self):
        """観点: load_repos 等のループ前段階で想定外例外が起きた時も :rotating_light: が付く。

        設定ファイル不正など「全リポ巻き添え」の重大エラーで、最も人間の即応が必要な経路。
        ここを :x: と同じ扱いにすると重要度が見分けにくくなるため :rotating_light: 固定。
        """
        with patch("review.load_repos", side_effect=RuntimeError("config broken")):
            with pytest.raises(SystemExit):
                main()
        assert self._notify_title().startswith(":rotating_light: ")
