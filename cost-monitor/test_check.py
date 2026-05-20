#!/usr/bin/env python3
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from check import (
    _actions_run_url,
    _build_error_header,
    load_environments,
    notify_slack,
)
from resources import (
    CommandError,
    _is_not_found,
    _run_cmd,
    check_environment,
    check_gke_nodes,
    check_ingress,
    check_psc,
    check_static_ips,
    format_cmd_failure,
    namespace_exists,
    setup_gke_credentials,
)


def _subprocess_result(returncode: int, stderr: str = "", stdout: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stderr = stderr
    result.stdout = stdout
    return result


class TestIsNotFound:
    """「リソース未存在」を示す stderr のパターン判定。

    重要: 認証失敗 / quota / network / permission denied 等は NotFound 扱いに
    してはならない。これらが NotFound 判定されると silent に「リソース無し」
    として返ってしまい、実際には権限切れでチェックできていないのに
    「コスト発生リソース無し」と誤った安心感を与えてしまう。
    """

    def test_gcloud_style_not_found(self):
        """観点: gcloud CLI が返す典型的な NotFound メッセージを拾う。"""
        assert _is_not_found("ERROR: (gcloud.sql.instances.describe) NOT_FOUND: The Cloud SQL instance does not exist.") is True

    def test_kubectl_style_not_found(self):
        """観点: kubectl が返す "not found" メッセージを拾う。"""
        assert _is_not_found('Error from server (NotFound): namespaces "dev" not found') is True

    def test_http_404_string(self):
        """観点: HTTP 404 を含むエラーレスポンスを NotFound として扱う。"""
        assert _is_not_found("Response: 404 Not Found") is True

    def test_case_insensitive(self):
        """観点: 大文字小文字問わずパターンマッチする。"""
        assert _is_not_found("not found") is True
        assert _is_not_found("NOT FOUND") is True
        assert _is_not_found("Not Found") is True

    def test_permission_denied_is_not_notfound(self):
        """観点: 権限不足を NotFound と誤判定しない（silent に安心させない）。"""
        assert _is_not_found("ERROR: permission denied") is False
        assert _is_not_found("PERMISSION_DENIED: missing role") is False

    def test_quota_exceeded_is_not_notfound(self):
        """観点: quota エラーを NotFound と誤判定しない。"""
        assert _is_not_found("RESOURCE_EXHAUSTED: quota exceeded") is False

    def test_network_error_is_not_notfound(self):
        """観点: ネットワークエラーを NotFound と誤判定しない。"""
        assert _is_not_found("connection refused") is False
        assert _is_not_found("timeout waiting for response") is False

    def test_empty_string_is_not_notfound(self):
        """観点: 空 stderr は NotFound と判定しない。"""
        assert _is_not_found("") is False

    def test_unrelated_text_containing_404_number(self):
        """観点: 数値 404 は \\b で単語境界を要求しているので誤爆しない。"""
        # "4040" は 404 の単語境界にマッチしないこと
        assert _is_not_found("status 4040") is False


class TestFormatCmdFailure:
    """外部コマンド失敗時の整形。

    stderr だけ見る実装にすると Claude CLI 等の「stdout にしかエラーを吐く CLI」
    の失敗原因が完全に消えるため、両方を必ず拾う仕様。
    """

    def test_stderr_only(self):
        """観点: 標準的な CLI（stderr にエラー）のパターン。"""
        assert format_cmd_failure("err msg", "", 1) == "err msg"

    def test_stdout_only(self):
        """観点: stdout にしかエラーを吐く CLI のパターン。"""
        assert format_cmd_failure("", "out msg", 1) == "stdout: out msg"

    def test_both_present(self):
        """観点: 両方ある場合は両方残す（情報欠落を避ける）。"""
        assert format_cmd_failure("err", "out", 1) == "stderr: err\nstdout: out"

    def test_both_empty_includes_exit_code(self):
        """観点: stderr/stdout が両方空でも exit code を載せて原因追跡の手がかりを残す。

        format_cmd_failure の戻り値は呼び出し側 (resources.py の _run_cmd /
        setup_gke_credentials など) で Actions ログに print されるだけで、
        exit code は別経路に流れない。両方空のときに exit code を含めないと、
        ログには "[label] " とラベルしか残らず「コマンドが失敗した」事実すら
        判別できなくなる。exit 127 (command not found) や exit 126
        (permission denied) のような切り分け情報を最低限の手がかりとして残す。
        """
        assert format_cmd_failure("", "", 127) == "exit code 127 (stderr/stdout ともに空)"

    def test_strips_surrounding_whitespace(self):
        """観点: 余分な空白は出力前に除去される。"""
        assert format_cmd_failure("  err\n", "  out\n", 1) == "stderr: err\nstdout: out"


class TestActionsRunUrl:
    """GitHub Actions 実行中かどうかで Slack に Actions run URL を載せる仕様。"""

    def test_all_env_vars_present(self):
        """観点: 必要な 3 つの環境変数が揃った時に URL を組み立てる。"""
        env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "kenyamaneko/overload-party-ops",
            "GITHUB_RUN_ID": "12345",
        }
        with patch.dict(os.environ, env, clear=True):
            assert _actions_run_url() == "https://github.com/kenyamaneko/overload-party-ops/actions/runs/12345"

    def test_missing_run_id_returns_empty(self):
        """観点: RUN_ID が欠けていれば URL を組み立てない（ローカル実行など）。"""
        env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "kenyamaneko/overload-party-ops",
        }
        with patch.dict(os.environ, env, clear=True):
            assert _actions_run_url() == ""

    def test_no_env_vars_returns_empty(self):
        """観点: 全ての環境変数が無い（ローカル実行）時は空文字で、URL 埋め込みをスキップできる。"""
        with patch.dict(os.environ, {}, clear=True):
            assert _actions_run_url() == ""

    def test_trailing_slash_stripped(self):
        """観点: GITHUB_SERVER_URL の末尾スラッシュで URL がダブルスラッシュにならない。"""
        env = {
            "GITHUB_SERVER_URL": "https://github.com/",
            "GITHUB_REPOSITORY": "org/repo",
            "GITHUB_RUN_ID": "1",
        }
        with patch.dict(os.environ, env, clear=True):
            assert _actions_run_url() == "https://github.com/org/repo/actions/runs/1"


class TestCheckGkeNodes:
    """env 専用 nodepool の Node 稼働チェックの仕様。

    GKE Standard は Node 単位で時間課金される。Pod 数や Deployment の
    spec.replicas は課金に直接影響しないため、消し忘れ検知は nodepool の
    Node 数で行う仕様の固定。
    """

    def _nodes_json(self, count: int) -> str:
        return json.dumps({"items": [{"metadata": {"name": f"n{i}"}} for i in range(count)]})

    def test_running_nodes_are_cost(self):
        """観点: nodepool に Node が存在すればコスト警告に載る。"""
        with patch("resources.kubectl_json", return_value=self._nodes_json(2)):
            costs, errors = check_gke_nodes("dev")
        assert len(costs) == 1
        assert "2 台稼働中" in costs[0]
        assert "keyandnotes-main-dev" in costs[0]
        assert errors == []

    def test_zero_nodes_is_not_cost(self):
        """観点: Node 0 台 (shutdown 済) は正常状態、エラー扱いしない。"""
        with patch("resources.kubectl_json", return_value=self._nodes_json(0)):
            costs, errors = check_gke_nodes("dev")
        assert costs == []
        assert errors == []

    def test_malformed_json_is_error(self):
        """観点: JSON パース失敗は errors に追加する。"""
        with patch("resources.kubectl_json", return_value="not-json"):
            costs, errors = check_gke_nodes("dev")
        assert costs == []
        assert len(errors) == 1
        assert "JSON パース失敗" in errors[0]


class TestCheckIngress:
    """Ingress 稼働チェックの仕様。"""

    def test_ingress_with_ip_is_cost(self):
        """観点: Ingress の LoadBalancer に IP が割り振られていれば稼働中としてコスト表示。"""
        ing = {"status": {"loadBalancer": {"ingress": [{"ip": "1.2.3.4"}]}}}
        with patch("resources.kubectl_json", return_value=json.dumps(ing)):
            costs, errors = check_ingress("dev")
        assert len(costs) == 1
        assert "1.2.3.4" in costs[0]
        assert errors == []

    def test_no_ip_assigned_is_not_cost(self):
        """観点: loadBalancer.ingress が空なら IP 未割り振り = 稼働していない扱い。"""
        ing = {"status": {"loadBalancer": {"ingress": []}}}
        with patch("resources.kubectl_json", return_value=json.dumps(ing)):
            costs, errors = check_ingress("dev")
        assert costs == []
        assert errors == []

    def test_missing_ip_field_is_error(self):
        """観点: ingress エントリがあるのに ip フィールドが無いのは API 異常としてエラー。

        silent に "unknown" 表示せず errors に流し、人間の調査を促す。
        """
        ing = {"status": {"loadBalancer": {"ingress": [{"hostname": "foo"}]}}}
        with patch("resources.kubectl_json", return_value=json.dumps(ing)):
            costs, errors = check_ingress("dev")
        assert costs == []
        assert len(errors) == 1
        assert "ip フィールド" in errors[0]

    def test_not_found_returns_empty(self):
        """観点: Ingress が存在しない（NotFound）場合は空でスキップ。"""
        with patch("resources.kubectl_json", return_value=""):
            costs, errors = check_ingress("dev")
        assert costs == []
        assert errors == []


class TestCheckStaticIps:
    """予約済み外部 IP チェックの仕様。"""

    def test_reserved_ips_are_costs(self):
        """観点: gcloud が返した予約済み IP は全てコスト表示対象。"""
        addrs = [
            {"name": "ip-a", "address": "1.1.1.1"},
            {"name": "ip-b", "address": "2.2.2.2"},
        ]
        with patch("resources.gcloud", return_value=json.dumps(addrs)):
            costs, errors = check_static_ips("proj")
        assert len(costs) == 2
        assert "ip-a" in costs[0] and "1.1.1.1" in costs[0]
        assert errors == []

    def test_empty_list_no_costs(self):
        """観点: 予約済み IP が 0 件なら空で返る。"""
        with patch("resources.gcloud", return_value="[]"):
            costs, errors = check_static_ips("proj")
        assert costs == []
        assert errors == []

    def test_missing_name_field_is_error(self):
        """観点: name フィールドが欠けていれば silent に "unknown" 表示せずエラー。"""
        addrs = [{"address": "1.1.1.1"}]
        with patch("resources.gcloud", return_value=json.dumps(addrs)):
            costs, errors = check_static_ips("proj")
        assert costs == []
        assert len(errors) == 1

    def test_missing_address_field_is_error(self):
        """観点: address フィールドが欠けていれば silent に "unknown" 表示せずエラー。"""
        addrs = [{"name": "ip-a"}]
        with patch("resources.gcloud", return_value=json.dumps(addrs)):
            costs, errors = check_static_ips("proj")
        assert costs == []
        assert len(errors) == 1

    def test_mixed_valid_and_invalid_entries(self):
        """観点: 壊れたエントリだけをエラーにし、正常なものはコストとして拾う。"""
        addrs = [
            {"name": "ok", "address": "1.1.1.1"},
            {"name": "broken"},  # address 欠落
        ]
        with patch("resources.gcloud", return_value=json.dumps(addrs)):
            costs, errors = check_static_ips("proj")
        assert len(costs) == 1
        assert "ok" in costs[0]
        assert len(errors) == 1


class TestCheckPsc:
    """PSC forwarding rule チェックの仕様。"""

    def test_psc_rules_are_costs(self):
        """観点: forwarding rule は稼働中としてコスト表示。"""
        rules = [{"name": "rule-a"}, {"name": "rule-b"}]
        with patch("resources.gcloud", return_value=json.dumps(rules)):
            costs, errors = check_psc("proj")
        assert len(costs) == 2
        assert "rule-a" in costs[0]
        assert errors == []

    def test_empty_list_no_costs(self):
        """観点: forwarding rule が 0 件なら空で返る。"""
        with patch("resources.gcloud", return_value="[]"):
            costs, errors = check_psc("proj")
        assert costs == []
        assert errors == []

    def test_missing_name_field_is_error(self):
        """観点: name フィールドが欠けていれば silent に "unknown" 表示せずエラー。"""
        with patch("resources.gcloud", return_value=json.dumps([{"target": "x"}])):
            costs, errors = check_psc("proj")
        assert costs == []
        assert len(errors) == 1


class TestCheckEnvironment:
    """環境単位の総合チェックの分岐仕様。"""

    @pytest.fixture(autouse=True)
    def _patches(self):
        with patch("resources.check_cloudsql", return_value=([], [])) as cloudsql, \
             patch("resources.check_gke_nodes", return_value=([], [])) as nodes, \
             patch("resources.check_ingress", return_value=([], [])) as ingress, \
             patch("resources.check_static_ips", return_value=([], [])) as static_ips, \
             patch("resources.check_psc", return_value=([], [])) as psc, \
             patch("resources.namespace_exists", return_value=(True, None)) as ns:
            self.cloudsql = cloudsql
            self.nodes = nodes
            self.ingress = ingress
            self.static_ips = static_ips
            self.psc = psc
            self.namespace_exists = ns
            yield

    def test_gke_unavailable_skips_gke_checks(self):
        """観点: GKE 認証に失敗していれば GKE 系チェックを全てスキップする。

        認証失敗時に GKE 系を叩くと全て失敗し、本質的に意味のない大量のエラーが
        Slack に並んでしまう。共通原因（認証失敗）は main で一度通知し、
        個別チェックはスキップするのが仕様。
        """
        check_environment("dev", "proj", gke_available=False)
        self.cloudsql.assert_called_once()
        self.static_ips.assert_called_once()
        self.psc.assert_called_once()
        # GKE 系は呼ばれない
        self.namespace_exists.assert_not_called()
        self.nodes.assert_not_called()
        self.ingress.assert_not_called()

    def test_missing_namespace_still_checks_nodes(self):
        """観点: namespace が無くても nodepool チェックは実行する。

        nodepool は cluster-scoped で namespace 存在に依存しない。namespace 削除
        済みでも nodepool が残っているケース (清掃漏れ) を検知するため、Node
        チェックは ns_ok 分岐の外で必ず実行する仕様の固定。namespace-scoped な
        Ingress はスキップする。
        """
        self.namespace_exists.return_value = (False, None)
        costs, errors = check_environment("dev", "proj")
        self.nodes.assert_called_once()
        self.ingress.assert_not_called()
        # Cloud SQL / 静的 IP / PSC は呼ばれる
        self.cloudsql.assert_called_once()
        self.static_ips.assert_called_once()
        self.psc.assert_called_once()
        assert errors == []

    def test_namespace_check_error_is_propagated(self):
        """観点: namespace 確認が API 失敗した場合、errors に詳細が流れる。

        認証失敗等の重大エラーを silent に「namespace 無し」扱いしないための仕様。
        """
        self.namespace_exists.return_value = (False, "認証失敗 (詳細はログ)")
        costs, errors = check_environment("dev", "proj")
        assert "認証失敗 (詳細はログ)" in errors
        # エラー時でも nodepool は cluster-scoped なので呼ぶ。Ingress は namespace
        # 依存なのでスキップ。
        self.nodes.assert_called_once()
        self.ingress.assert_not_called()

    def test_all_checks_run_when_namespace_exists(self):
        """観点: namespace がある正常ケースでは全チェックが実行される。"""
        check_environment("dev", "proj")
        self.cloudsql.assert_called_once()
        self.nodes.assert_called_once()
        self.ingress.assert_called_once()
        self.static_ips.assert_called_once()
        self.psc.assert_called_once()

    def test_costs_and_errors_are_merged(self):
        """観点: 各チェックの costs/errors が集約されて返る。"""
        self.cloudsql.return_value = (["Cloud SQL 稼働中"], [])
        self.nodes.return_value = (["Nodepool 2 台稼働中"], ["nodepool チェック失敗"])
        costs, errors = check_environment("dev", "proj")
        assert "Cloud SQL 稼働中" in costs
        assert "Nodepool 2 台稼働中" in costs
        assert "nodepool チェック失敗" in errors


class TestRunCmd:
    """外部コマンド実行ラッパー。_is_not_found の判定結果を
    「NotFound → silent skip / それ以外 → 例外で上げる」の分岐に繋げる要所。

    _is_not_found 単体が正確でも、ここが判定を間違った分岐に繋げたら silent failure
    になるため、分岐パスを直接固定する。
    """

    def test_success_returns_stripped_stdout(self):
        """観点: exit 0 時は stdout を strip して返す。"""
        with patch("resources.subprocess.run", return_value=_subprocess_result(0, stdout="  hello\n")):
            assert _run_cmd(["fake"], label="test") == "hello"

    def test_not_found_with_allow_returns_empty(self):
        """観点: allow_not_found=True + NotFound stderr → 空文字で抜ける（許可された silent）。"""
        stderr = "ERROR: (gcloud.sql.instances.describe) NOT_FOUND: gone"
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr=stderr)):
            assert _run_cmd(["fake"], label="test", allow_not_found=True) == ""

    def test_permission_denied_with_allow_still_raises(self):
        """観点: allow_not_found=True でも permission denied は NotFound 扱いしない。

        これが最重要。_is_not_found が PERMISSION_DENIED を False 判定しても、
        _run_cmd がそれを活かせないと silent failure になる。判定結果→分岐の結合を固定。
        """
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="ERROR: permission denied")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="test", allow_not_found=True)

    def test_network_error_with_allow_still_raises(self):
        """観点: ネットワークエラー/timeout も NotFound 扱いにしない。"""
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="connection refused")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="test", allow_not_found=True)

    def test_not_found_without_allow_raises(self):
        """観点: allow_not_found=False なら NotFound stderr でも例外（strict モード）。

        呼び出し側が明示的に許可していない限り、NotFound でも未定義の状態として
        エラーで止める仕様。
        """
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="NOT_FOUND")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="test", allow_not_found=False)

    def test_empty_stderr_with_allow_raises(self):
        """観点: stderr 空 → NotFound 判定できず例外（空入力を silent 許可しない）。

        `if allow_not_found and stderr and _is_not_found(stderr):` の
        中間 `and stderr` の存在意義の固定。空 stderr を NotFound と誤判定して
        silent に抜ける経路を作らない。
        """
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="", stdout="")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="test", allow_not_found=True)

    def test_failure_prints_format_cmd_failure_detail(self, capsys):
        """観点: 失敗時に format_cmd_failure の出力が print される（Actions ログへの到達保証）。

        Slack に行くのは短い CommandError だけ。詳細はこの print 経由でしか残らないため、
        ログへの到達を固定しないと format_cmd_failure 単体テストが無意味になる。
        """
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="actual failure")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="mylabel")
        captured = capsys.readouterr()
        assert "mylabel" in captured.out
        assert "actual failure" in captured.out

    def test_commanderror_contains_label_and_exit_code(self):
        """観点: CommandError に label と exit code が含まれる。

        Slack には短いメッセージだけ届くため、Actions ログ検索の手がかりとして
        label と exit code の両方が必須。
        """
        with patch("resources.subprocess.run", return_value=_subprocess_result(42, stderr="fail")):
            with pytest.raises(CommandError, match=r"mylabel 実行失敗 \(exit 42\)"):
                _run_cmd(["fake"], label="mylabel")


class TestNamespaceExists:
    """namespace 存在チェック。独自に _is_not_found を呼ぶ箇所。

    stderr と stdout を combined して判定する仕様が要所。
    permission denied を silent に「namespace 無し」と扱わないことを固定する。
    """

    def test_success_returns_true_none(self):
        """観点: returncode=0 なら (True, None)。"""
        with patch("resources.subprocess.run", return_value=_subprocess_result(0)):
            assert namespace_exists("dev") == (True, None)

    def test_not_found_in_stderr_is_silent_skip(self):
        """観点: stderr に NotFound → (False, None) で正常スキップ。"""
        stderr = 'Error from server (NotFound): namespaces "dev" not found'
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr=stderr)):
            assert namespace_exists("dev") == (False, None)

    def test_not_found_in_stdout_only_is_also_silent_skip(self):
        """観点: stderr が空で stdout 側に NotFound が出るケースでも拾う（combined 判定の仕様）。

        kubectl は通常 stderr に出すが、CLI の挙動変化で stdout に出た時に
        silent failure にならないための仕様固定。
        """
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="", stdout="NotFound")):
            assert namespace_exists("dev") == (False, None)

    def test_permission_denied_is_not_silent(self):
        """観点: permission denied を NotFound と誤判定せず、エラー詳細を返す。

        silent に (False, None) 扱いすると「namespace 無し → GKE チェックスキップ」
        と誤解され、認証失敗で見えていないだけなのに「コスト発生なし」と誤通知する。
        この silent 化を絶対に作らないための固定テスト。
        """
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="permission denied")):
            ok, err = namespace_exists("dev")
        assert ok is False
        assert err is not None
        assert "dev" in err
        assert "確認失敗" in err

    def test_empty_failure_still_returns_detail(self):
        """観点: stderr/stdout 両方空でも exit != 0 なら (False, 詳細) を返す。

        空 stderr を _is_not_found に渡すと False が返るため、NotFound 判定に
        落ちない → エラー経路に入るのが仕様。固定しないと将来のリファクタで
        「空 stderr を NotFound 扱い」する silent failure が紛れる。
        """
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="", stdout="")):
            ok, err = namespace_exists("dev")
        assert ok is False
        assert err is not None

    def test_failure_logs_detail(self, capsys):
        """観点: 非 NotFound エラーの詳細が print で Actions ログに残る。"""
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="specific RBAC failure")):
            namespace_exists("dev")
        captured = capsys.readouterr()
        assert "specific RBAC failure" in captured.out


class TestSetupGkeCredentials:
    """GKE 認証は main で (shared) エラーとして Slack に流す起点。"""

    def test_success_returns_true_none(self):
        """観点: returncode=0 なら (True, None)。"""
        with patch("resources.subprocess.run", return_value=_subprocess_result(0)):
            assert setup_gke_credentials() == (True, None)

    def test_failure_returns_short_message_for_slack(self):
        """観点: 失敗時は短い固定メッセージを返す（Slack の "(shared)" 枠に載る内容）。

        長い stderr をそのまま Slack に載せると読みづらいため、短く固定し、
        詳細は print 経由で Actions ログに逃がすのが仕様。
        """
        result = _subprocess_result(1, stderr="detailed auth error from gcloud")
        with patch("resources.subprocess.run", return_value=result):
            ok, err = setup_gke_credentials()
        assert ok is False
        assert err == "GKE 認証失敗 (詳細はログ)"

    def test_failure_logs_detail(self, capsys):
        """観点: Slack の短いメッセージからログへ辿れるように、詳細が print される。"""
        result = _subprocess_result(1, stderr="specific auth failure")
        with patch("resources.subprocess.run", return_value=result):
            setup_gke_credentials()
        captured = capsys.readouterr()
        assert "specific auth failure" in captured.out


class TestLoadEnvironments:
    """監視対象環境の読み込み。ENVIRONMENTS_JSON 優先 → YAML フォールバック。"""

    def test_env_json_takes_precedence(self):
        """観点: ENVIRONMENTS_JSON が設定されていれば YAML より優先される。"""
        env = {"ENVIRONMENTS_JSON": '{"dev": "proj-dev", "stg": "proj-stg"}'}
        with patch.dict(os.environ, env, clear=True):
            result = load_environments()
        assert result == {"dev": "proj-dev", "stg": "proj-stg"}

    def test_invalid_json_raises(self):
        """観点: ENVIRONMENTS_JSON が不正な JSON なら例外で落とす（silent に YAML に逃げない）。

        silent に YAML にフォールバックすると設定ミスが露見せず、意図と違う環境を
        監視し続ける事故になる。
        """
        env = {"ENVIRONMENTS_JSON": "not-json"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(json.JSONDecodeError):
                load_environments()

    def test_fallback_to_yaml(self, tmp_path):
        """観点: ENVIRONMENTS_JSON 未設定時は YAML を読む。"""
        yaml_file = tmp_path / "environments.yaml"
        yaml_file.write_text("dev: proj-dev\nstg: proj-stg\n")
        with patch.dict(os.environ, {}, clear=True), \
             patch("check.ENVIRONMENTS_YAML", yaml_file):
            result = load_environments()
        assert result == {"dev": "proj-dev", "stg": "proj-stg"}


class TestNotifySlack:
    """Slack 通知の失敗時挙動。通知経路そのもののテスト。"""

    def test_success_calls_urlopen(self):
        """観点: 正常時に urllib.request.urlopen を 1 回呼ぶ。"""
        with patch("check.urllib.request.urlopen") as urlopen:
            notify_slack("https://webhook", "msg")
        urlopen.assert_called_once()

    def test_failure_exits_1(self):
        """観点: Slack 通知失敗は sys.exit(1) でプロセス失敗にする。

        通知が届かないと誰もエラーに気付けないため、Actions Job を失敗させて
        ジョブ失敗メール/通知で気付かせる二重化。
        """
        with patch("check.urllib.request.urlopen", side_effect=Exception("boom")):
            with pytest.raises(SystemExit) as exc:
                notify_slack("https://webhook", "msg")
        assert exc.value.code == 1


class TestCheckCommandErrorPaths:
    """各 check_* 関数が CommandError を受け取った時、Slack に届く errors に
    意味のあるメッセージを積むことを固定する。

    既存テストは `check.gcloud` / `check.kubectl_json` の return_value を
    差し替えていて、CommandError 例外パスを通っていない。呼び出し側の
    except 節の存在と、積まれる文字列の質を直接検証する。
    """

    def test_check_cloudsql_commanderror_produces_readable_message(self):
        from resources import check_cloudsql
        with patch("resources.gcloud_value", side_effect=CommandError("gcloud 実行失敗 (exit 1)")):
            costs, errors = check_cloudsql("proj")
        assert costs == []
        assert len(errors) == 1
        assert "Cloud SQL" in errors[0]
        assert "gcloud 実行失敗" in errors[0]

    def test_check_ingress_commanderror_produces_readable_message(self):
        with patch("resources.kubectl_json", side_effect=CommandError("kubectl 実行失敗 (exit 1)")):
            costs, errors = check_ingress("dev")
        assert costs == []
        assert len(errors) == 1
        assert "Ingress" in errors[0]
        assert "kubectl" in errors[0]

    def test_check_static_ips_commanderror_produces_readable_message(self):
        with patch("resources.gcloud", side_effect=CommandError("gcloud 実行失敗 (exit 1)")):
            costs, errors = check_static_ips("proj")
        assert costs == []
        assert len(errors) == 1
        assert "外部 IP" in errors[0]

    def test_check_psc_commanderror_produces_readable_message(self):
        with patch("resources.gcloud", side_effect=CommandError("gcloud 実行失敗 (exit 1)")):
            costs, errors = check_psc("proj")
        assert costs == []
        assert len(errors) == 1
        assert "PSC" in errors[0]

    def test_check_gke_nodes_commanderror_produces_readable_message(self):
        """観点: nodepool 取得失敗が errors に readable で残る。"""
        with patch("resources.kubectl_json", side_effect=CommandError("kubectl 実行失敗 (exit 1)")):
            costs, errors = check_gke_nodes("dev")
        assert costs == []
        assert len(errors) == 1
        assert "Nodepool" in errors[0]
        assert "チェック失敗" in errors[0]


class TestBuildErrorHeader:
    """Slack 通知のエラーヘッダ。ログ詳細への動線を必ず載せる仕様。

    Slack に載る短いエラー文（「gcloud 実行失敗 (exit 1)」等）だけでは
    原因追跡が不可能なため、Actions URL か「URL 取得不可」の明示を
    必ず含めることで、ユーザーがどこを見ればいいか迷わないようにする。
    """

    def test_url_available_includes_link(self):
        """観点: Actions URL が取れる時は Slack リンク形式で埋め込む。"""
        header = _build_error_header(
            "2026-04-17", "https://github.com/org/repo/actions/runs/12345",
        )
        assert "https://github.com/org/repo/actions/runs/12345" in header
        assert "<https://github.com/org/repo/actions/runs/12345|ログ>" in header

    def test_url_missing_explicitly_states_absence(self):
        """観点: URL 取得不可の時も「動線なし」を明示して調査の起点を与える。

        silent に URL を省略するとユーザーは「なぜログ無いのか」分からず調査を
        諦める。必ず「取得不可」と理由を載せて問い合わせの起点になる文言にする。
        """
        header = _build_error_header("2026-04-17", "")
        assert "ログ URL 取得不可" in header
        # ユーザーが見るべき代替動線（stdout）を明示
        assert "stdout" in header

    def test_always_includes_date_and_error_marker(self):
        """観点: URL 有無に関わらず日付とエラーマーカー（:x:）は常に含まれる。"""
        with_url = _build_error_header("2026-04-17", "https://x/y")
        without_url = _build_error_header("2026-04-17", "")
        for h in [with_url, without_url]:
            assert "2026-04-17" in h
            assert ":x:" in h
            assert "チェックエラー" in h
