#!/usr/bin/env python3
import json
from unittest.mock import MagicMock, patch

import pytest
import check
from check import (
    SLACK_TEXT_LIMIT,
    PlanParseError,
    _strip_init_noise,
    clone_repo,
    extract_summary,
    load_targets,
    notify_slack,
    terraform_plan,
)


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestStripInitNoise:
    """terraform init のボイラープレート行を除去する仕様。

    エラー時の Slack 通知に init ログが混ざるとノイズで読みづらくなるため、
    init の典型メッセージだけ削り、エラー本体は残すことが目的。
    """

    def test_removes_initializing_lines(self):
        """観点: "Initializing ..." 系のボイラープレート行が削られる。"""
        out = (
            "Initializing the backend...\n"
            "Initializing provider plugins...\n"
            "Terraform has been successfully initialized!\n"
            "\n"
            "Error: actual failure message\n"
        )
        cleaned = _strip_init_noise(out)
        assert "Initializing" not in cleaned
        assert "successfully initialized" not in cleaned
        assert "Error: actual failure message" in cleaned

    def test_removes_provider_install_lines(self):
        """観点: provider インストール系の行（- Reusing/Using/Installing）が削られる。"""
        out = (
            "- Reusing previous version of hashicorp/google\n"
            "- Using previously-installed hashicorp/google v6.0.0\n"
            "- Installing hashicorp/google v6.0.0\n"
            "- Installed hashicorp/google v6.0.0\n"
            "real error here\n"
        )
        cleaned = _strip_init_noise(out)
        assert "Reusing" not in cleaned
        assert "previously-installed" not in cleaned
        assert "Installing" not in cleaned
        assert "Installed" not in cleaned
        assert "real error here" in cleaned

    def test_preserves_error_content(self):
        """観点: エラー本文は削られずそのまま残る。"""
        out = "Error: Failed to query available provider packages\n\nCould not retrieve the list of available versions"
        cleaned = _strip_init_noise(out)
        assert "Failed to query available provider packages" in cleaned
        assert "Could not retrieve the list of available versions" in cleaned

    def test_drops_empty_lines(self):
        """観点: 空行は削られ、Slack 上の無駄な改行を減らす。"""
        out = "line1\n\n\nline2\n"
        cleaned = _strip_init_noise(out)
        assert cleaned == "line1\nline2"

    def test_empty_input(self):
        """観点: 空入力でも例外を投げず空文字列を返す境界条件。"""
        assert _strip_init_noise("") == ""


class TestExtractSummary:
    """terraform plan 出力から drift 内容を抽出する仕様。

    drift 検出時 (exit_code=2) の出力は必ず Plan: 行か `# ... will be ...` 行を
    含むため、両方が無い場合は silent に末尾を載せず例外で人間の調査を促す。
    """

    def test_extracts_plan_line(self):
        """観点: "Plan: X to add, Y to change, Z to destroy" 行が要約として拾われる。"""
        out = (
            "Terraform will perform the following actions:\n"
            "\n"
            "  # google_storage_bucket.foo will be created\n"
            "\n"
            "Plan: 1 to add, 0 to change, 0 to destroy.\n"
        )
        summary = extract_summary(out)
        assert "Plan: 1 to add, 0 to change, 0 to destroy." in summary

    def test_extracts_changed_resources(self):
        """観点: `# <resource> will be ...` 行が変更対象リソースリストとして抽出される。"""
        out = (
            "  # google_storage_bucket.a will be created\n"
            "  # google_storage_bucket.b will be updated in-place\n"
            "Plan: 2 to add, 0 to change, 0 to destroy.\n"
        )
        summary = extract_summary(out)
        assert "google_storage_bucket.a will be created" in summary
        assert "google_storage_bucket.b will be updated in-place" in summary

    def test_resources_limited_to_10_with_overflow_note(self):
        """観点: 11 個以上の変更対象は 10 件まで + 「他N件」に集約される。

        Slack 通知の読みやすさ確保と文字数制限への対応仕様。
        """
        resource_lines = "\n".join(
            f"  # aws_instance.node_{i} will be created" for i in range(15)
        )
        out = resource_lines + "\nPlan: 15 to add, 0 to change, 0 to destroy.\n"
        summary = extract_summary(out)
        # 最初の 10 個は載る
        for i in range(10):
            assert f"aws_instance.node_{i}" in summary
        # 11 個目以降は載らず、代わりに集約行が入る
        assert "aws_instance.node_10" not in summary
        assert "他 5 リソース" in summary

    def test_no_changes_is_summary(self):
        """観点: "No changes" 行も summary として拾う。

        terraform の表現揺れ（"No changes. Your infrastructure matches..." 等）を
        想定しているが、実運用では drift 検出時に呼ばれるため通常は Plan: 行が出る。
        """
        out = "No changes. Your infrastructure matches the configuration.\n"
        summary = extract_summary(out)
        assert "No changes" in summary

    def test_parse_failure_raises(self):
        """観点: Plan: 行も変更対象リソースも検出できなければ PlanParseError を投げる。

        意図: parse 失敗を silent に扱うと「drift あり、内容不明」のミスリード通知になるため
        例外化して人間の調査を促す。terraform 出力フォーマット変更の早期検知も兼ねる。
        """
        out = (
            "Some unexpected terraform output format\n"
            "that contains neither Plan: nor will be lines\n"
            "end of output\n"
        )
        with pytest.raises(PlanParseError):
            extract_summary(out)

    def test_empty_input_raises(self):
        """観点: 空文字列も parse 失敗として例外化（silent に空要約を返さない）。"""
        with pytest.raises(PlanParseError):
            extract_summary("")


class TestLoadTargets:
    """targets.yaml の読み込み。ファイル不在は silent に [] を返さず例外化する。"""

    def test_loads_yaml(self, tmp_path):
        """観点: targets.yaml の内容が list[dict] としてロードされる。"""
        yaml_file = tmp_path / "targets.yaml"
        yaml_file.write_text("- repo: a\n  environments: []\n")
        with patch("check.TARGETS_YAML", yaml_file):
            result = load_targets()
        assert result == [{"repo": "a", "environments": []}]

    def test_missing_raises(self, tmp_path):
        """観点: targets.yaml が存在しなければ FileNotFoundError を投げる。

        silent に [] を返すと「監視対象ゼロ = 全環境クリア」と誤認される事故を招く。
        """
        missing = tmp_path / "nope.yaml"
        with patch("check.TARGETS_YAML", missing):
            with pytest.raises(FileNotFoundError):
                load_targets()


class TestCloneRepo:
    """git clone のエラーを stderr に残しつつ None を返して呼び出し側に委譲する仕様。"""

    def test_success_returns_dest_path(self):
        """観点: clone 成功時は dest path を返す。"""
        with patch("check.run", return_value=_proc(0)):
            result = clone_repo("repo-x", "token")
        assert result is not None
        assert "repo-x" in result

    def test_failure_returns_none(self):
        """観点: clone 失敗時は None を返し、呼び出し側で errors に積ませる。

        例外で落とさず None で伝えるのは、他のリポジトリの plan を続けるため。
        """
        with patch("check.run", return_value=_proc(1, stderr="auth failed")):
            assert clone_repo("repo-x", "token") is None

    def test_failure_logs_stderr_with_repo_name(self, capsys):
        """観点: 失敗詳細が stderr に出る。silent failure 防止。

        リポ名を含めないと、どのリポで何が失敗したか Actions ログから追えなくなる。
        """
        with patch("check.run", return_value=_proc(1, stderr="auth failed")):
            clone_repo("repo-x", "token")
        captured = capsys.readouterr()
        assert "auth failed" in captured.err
        assert "repo-x" in captured.err


class TestTerraformPlan:
    """terraform init + plan の 3 分岐 (exit 0 / 2 / その他)。

    -detailed-exitcode の仕様:
      exit 0 = 差分なし / exit 2 = drift 検出 / exit 1 = エラー
    これを正しく分類できないと drift を「エラー」として silent 通知する事故になる。
    """

    def test_init_failure_returns_1(self):
        """観点: init が失敗したら plan を実行せず (1, detail) を返す。"""
        with patch("check.run", return_value=_proc(1, stderr="init failed")):
            code, output = terraform_plan("/work")
        assert code == 1
        assert "init failed" in output

    def test_init_failure_does_not_run_plan(self):
        """観点: init 失敗時に plan を叩かない（無駄な API 呼びを防ぐ）。"""
        init_fail = _proc(1, stderr="init failed")
        with patch("check.run", side_effect=[init_fail]) as run_mock:
            terraform_plan("/work")
        # plan が呼ばれたら StopIteration で落ちる → 呼ばれていないことを確認
        assert run_mock.call_count == 1

    def test_no_drift_returns_0(self):
        """観点: plan exit 0 → 差分なし。"""
        with patch("check.run", side_effect=[_proc(0), _proc(0, stdout="No changes.")]):
            code, _ = terraform_plan("/work")
        assert code == 0

    def test_drift_detected_returns_2(self):
        """観点: plan exit 2 → drift。detailed-exitcode の仕様を silent に 0 扱いしない。"""
        with patch("check.run", side_effect=[_proc(0), _proc(2, stdout="Plan: 1 to add")]):
            code, output = terraform_plan("/work")
        assert code == 2
        assert "Plan: 1 to add" in output

    def test_plan_error_returns_1(self):
        """観点: plan が 非0/非2 → (1, output) で error 扱い。

        drift (exit 2) と区別しないと、provider error が drift 通知として
        Slack に流れる誤通知を生む。
        """
        with patch("check.run", side_effect=[_proc(0), _proc(1, stderr="provider error")]):
            code, _ = terraform_plan("/work")
        assert code == 1

    def test_plan_error_prefers_stderr(self):
        """観点: error 時は stderr → stdout の順で詳細を選ぶ（詳細が stderr に出る CLI の慣習）。"""
        with patch("check.run", side_effect=[
            _proc(0),
            _proc(1, stdout="stdout content", stderr="stderr detail"),
        ]):
            _, output = terraform_plan("/work")
        assert "stderr detail" in output


class TestNotifySlackTruncation:
    """Slack 通知 payload の truncate と失敗時挙動を固定。

    Slack API は 4000 chars 超過で通知そのものを失敗させる。
    ユーザーが何も気付けない silent failure を避けるための truncate 仕様を固定する。
    """

    def _capture_payload(self):
        captured = {}
        def _urlopen(req):
            captured["data"] = req.data.decode()
            return MagicMock()
        return captured, _urlopen

    def test_normal_message_posts(self):
        """観点: 正常時に urlopen が呼ばれる。"""
        with patch("check.urllib.request.urlopen") as urlopen:
            notify_slack("https://webhook", "hello")
        urlopen.assert_called_once()

    def test_over_limit_payload_fits_slack_api_max(self):
        """観点: 長大メッセージでも Slack API の 4000 文字制限内に収まり、truncate マーカーが付く。

        Slack API は text > 4000 で通知自体を失敗させる。ここで検証すべきは
        「制限を超えたときに Slack が受け付ける形に収まるか」であり、内部定数
        SLACK_TEXT_LIMIT の値そのものではない（定数は実装詳細）。
        """
        captured, urlopen = self._capture_payload()
        with patch("check.urllib.request.urlopen", side_effect=urlopen):
            notify_slack("https://webhook", "x" * 10000)
        payload = json.loads(captured["data"])
        assert "…(truncated)" in payload["text"]
        assert len(payload["text"]) < 4000

    def test_exactly_at_limit_is_not_truncated(self):
        """観点: SLACK_TEXT_LIMIT ちょうどなら truncate しない（>= ではなく > 条件の境界）。"""
        captured, urlopen = self._capture_payload()
        with patch("check.urllib.request.urlopen", side_effect=urlopen):
            notify_slack("https://webhook", "x" * SLACK_TEXT_LIMIT)
        payload = json.loads(captured["data"])
        assert "truncated" not in payload["text"]

    def test_webhook_failure_exits_1(self):
        """観点: urlopen 例外時は sys.exit(1) で Actions Job 失敗させる。

        Slack への到達が唯一の可視化経路なので、届かなかった時は
        プロセス終了コードで二重に気付かせる。
        """
        with patch("check.urllib.request.urlopen", side_effect=Exception("boom")):
            with pytest.raises(SystemExit) as exc:
                notify_slack("https://webhook", "x")
        assert exc.value.code == 1


class TestMainErrorPropagation:
    """terraform_plan のエラーが Slack 通知まで届くことを end-to-end で検証する。

    個々のユニットテストは戻り値までしか見ないため、main() の errors 積み込みや
    メッセージ組み立てで detail が欠落しても検知できない。ユーザーが異常に気付ける
    唯一の経路である Slack payload を起点に保証する。
    """

    def _run_main_with_plan_result(self, plan_exit_code: int, plan_output: str) -> str:
        """main() を 1 target で実行し、notify_slack に渡された message を返す。"""
        captured: dict = {}

        def fake_notify(webhook_url: str, message: str) -> None:
            captured["message"] = message

        env = {"GITHUB_TOKEN": "t", "SLACK_WEBHOOK_URL": "https://webhook"}
        targets = [{"repo": "r", "environments": [{"name": "e", "path": "p"}]}]

        with patch.dict("os.environ", env, clear=False), \
             patch("check.load_targets", return_value=targets), \
             patch("check.clone_repo", return_value="/tmp/r"), \
             patch("check.os.makedirs"), \
             patch("check.os.path.isdir", return_value=True), \
             patch("check.terraform_plan", return_value=(plan_exit_code, plan_output)), \
             patch("check.notify_slack", side_effect=fake_notify):
            check.main()
        return captured.get("message", "")

    def test_plan_error_detail_reaches_slack(self):
        """観点: terraform_plan が返した error detail が Slack payload に載る。

        意図: main() で detail を errors に積んで Slack メッセージに埋め込むパスが
        黙殺されると、ユーザーは error が起きたこと自体に気付けなくなる。
        """
        message = self._run_main_with_plan_result(1, "provider auth failed: 401")
        assert "plan 実行エラー" in message
        assert "r/e" in message
        assert "provider auth failed: 401" in message

    def test_drift_summary_reaches_slack(self):
        """観点: drift 検出時は extract_summary の結果が Slack payload に載る。

        意図: エラー経路と対になるハッピーパスの end-to-end 確認。
        """
        plan_out = "  # google_storage_bucket.a will be created\nPlan: 1 to add, 0 to change, 0 to destroy.\n"
        message = self._run_main_with_plan_result(2, plan_out)
        assert "差分を検出" in message
        assert "r/e" in message
        assert "Plan: 1 to add, 0 to change, 0 to destroy." in message
        assert "google_storage_bucket.a will be created" in message
