#!/usr/bin/env python3
import json
import os
from unittest.mock import MagicMock, patch

import pytest
import check
from check import (
    _build_error_header,
    build_actions_run_url,
    load_environments,
    notify_slack,
)
from resources import (
    CommandError,
    _is_not_found,
    _run_cmd,
    check_cloudsql,
    check_environment,
    check_psc,
    check_static_ips,
    format_cmd_failure,
    run_gcloud,
    run_gcloud_value,
)


def _subprocess_result(returncode: int, stderr: str = "", stdout: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stderr = stderr
    result.stdout = stdout
    return result


class Testリソース未存在の判定:
    """認証失敗 / quota / network / permission denied を NotFound と誤判定すると、
    実際には権限切れ等でチェックできていないのに silent に「リソース無し」を返し、
    「コスト発生リソース無し」と誤った安心感を与えるため、これらは NotFound としない。
    """

    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            pytest.param(
                "ERROR: (gcloud.sql.instances.describe) NOT_FOUND: The Cloud SQL instance does not exist.",
                True,
                id="gcloud の NOT_FOUND はリソース未存在",
            ),
            pytest.param(
                "ERROR: (gcloud.compute.addresses.describe) Could not be found: ip-dev",
                True,
                id="could not be found はリソース未存在",
            ),
            pytest.param("Response: 404 Not Found", True, id="HTTP 404 はリソース未存在"),
            pytest.param(
                "RESOURCE_EXHAUSTED: quota exceeded",
                False,
                id="quota 超過はリソース未存在としない",
            ),
            pytest.param("", False, id="空 stderr はリソース未存在としない"),
            # 404 は \b 単語境界を要求するため "4040" 等の数字列に誤爆しない
            pytest.param("status 4040", False, id="404 を含む 4040 は単語境界で誤爆しない"),
        ],
    )
    def test_stderrからリソース未存在かを判定する(self, stderr, expected):
        assert _is_not_found(stderr) is expected

    def test_大文字小文字を問わずリソース未存在と判定する(self):
        assert _is_not_found("not found") is True
        assert _is_not_found("NOT FOUND") is True
        assert _is_not_found("Not Found") is True

    def test_permission_deniedはリソース未存在としない(self):
        assert _is_not_found("ERROR: permission denied") is False
        assert _is_not_found("PERMISSION_DENIED: missing role") is False

    def test_ネットワークエラーはリソース未存在としない(self):
        assert _is_not_found("connection refused") is False
        assert _is_not_found("timeout waiting for response") is False


class Testコマンド失敗の整形:
    """stderr だけ見る実装にすると Claude CLI 等の「stdout にしかエラーを吐く CLI」の
    失敗原因が完全に消えるため、stderr と stdout の両方を必ず拾う。
    """

    @pytest.mark.parametrize(
        ("stderr", "stdout", "exit_code", "expected"),
        [
            pytest.param("err msg", "", 1, "err msg", id="stderr のみのとき、stderr をそのまま返す"),
            pytest.param("", "out msg", 1, "stdout: out msg", id="stdout のみのとき、stdout: を前置する"),
            pytest.param("err", "out", 1, "stderr: err\nstdout: out", id="両方あるとき、両方を残す"),
            # 両方空だと呼び出し側のログに "[label] " しか残らず失敗事実すら判別できないため、
            # exit 127 (command not found) / 126 (permission denied) 等の切り分けに exit code を残す
            pytest.param("", "", 127, "exit code 127 (stderr/stdout ともに空)", id="両方空のとき、exit code を載せる"),
            pytest.param("  err\n", "  out\n", 1, "stderr: err\nstdout: out", id="前後の空白は除去される"),
        ],
    )
    def test_失敗内容を整形する(self, stderr, stdout, exit_code, expected):
        assert format_cmd_failure(stderr, stdout, exit_code) == expected


class TestActions実行URLの組み立て:
    @pytest.mark.parametrize(
        ("env", "want"),
        [
            pytest.param(
                {
                    "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_REPOSITORY": "kenyamaneko/overload-party-ops",
                    "GITHUB_RUN_ID": "12345",
                },
                "https://github.com/kenyamaneko/overload-party-ops/actions/runs/12345",
                id="必要な 3 つの環境変数が揃うとき、完全な run URL になる",
            ),
            pytest.param(
                {
                    "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_REPOSITORY": "kenyamaneko/overload-party-ops",
                },
                "",
                id="RUN_ID が欠けるとき、空文字になる（ローカル実行など）",
            ),
            pytest.param({}, "", id="環境変数が無いとき、空文字になる"),
            pytest.param(
                {
                    "GITHUB_SERVER_URL": "https://github.com/",
                    "GITHUB_REPOSITORY": "org/repo",
                    "GITHUB_RUN_ID": "1",
                },
                "https://github.com/org/repo/actions/runs/1",
                id="server_url が末尾スラッシュ付きでも二重スラッシュにならない",
            ),
        ],
    )
    def test_環境変数からActions実行URLを組み立てる(self, env, want):
        with patch.dict(os.environ, env, clear=True):
            assert build_actions_run_url() == want


class TestCloudSQLの稼働チェック:
    def test_stateがRUNNABLEのときCloudSQLの時間課金がコスト警告に載る(self):
        with patch("resources.run_gcloud_value", return_value="RUNNABLE"):
            costs, errors = check_cloudsql("proj")
        assert costs == ["Cloud SQL `overload-party-db` が RUNNABLE ($0.19/hr)"]
        assert errors == []

    def test_インスタンスが存在しないときコストもエラーも報告しない(self):
        with patch("resources.run_gcloud_value", return_value=""):
            costs, errors = check_cloudsql("proj")
        assert costs == []
        assert errors == []

    @pytest.mark.parametrize(
        "state",
        [
            pytest.param("SUSPENDED", id="state が SUSPENDED のとき、稼働コストなしとして何も報告しない"),
            pytest.param("STOPPED", id="state が STOPPED のとき、稼働コストなしとして何も報告しない"),
        ],
    )
    def test_非稼働状態のCloudSQLはコストとして扱われない(self, state):
        with patch("resources.run_gcloud_value", return_value=state):
            costs, errors = check_cloudsql("proj")
        assert costs == []
        assert errors == []


class Test予約済み外部IPのチェック:
    def test_予約済みIPは全てコスト表示対象になる(self):
        addrs = [
            {"name": "ip-a", "address": "1.1.1.1"},
            {"name": "ip-b", "address": "2.2.2.2"},
        ]
        with patch("resources.run_gcloud", return_value=json.dumps(addrs)):
            costs, errors = check_static_ips("proj")
        assert len(costs) == 2
        assert "ip-a" in costs[0] and "1.1.1.1" in costs[0]
        assert errors == []

    def test_予約済みIPが0件なら空で返る(self):
        with patch("resources.run_gcloud", return_value="[]"):
            costs, errors = check_static_ips("proj")
        assert costs == []
        assert errors == []

    def test_nameフィールドが欠ければsilentにunknown表示せずエラー(self):
        addrs = [{"address": "1.1.1.1"}]
        with patch("resources.run_gcloud", return_value=json.dumps(addrs)):
            costs, errors = check_static_ips("proj")
        assert costs == []
        assert len(errors) == 1

    def test_addressフィールドが欠ければsilentにunknown表示せずエラー(self):
        addrs = [{"name": "ip-a"}]
        with patch("resources.run_gcloud", return_value=json.dumps(addrs)):
            costs, errors = check_static_ips("proj")
        assert costs == []
        assert len(errors) == 1

    def test_壊れたエントリだけエラーにし正常なものはコストで拾う(self):
        addrs = [
            {"name": "ok", "address": "1.1.1.1"},
            {"name": "broken"},  # address 欠落
        ]
        with patch("resources.run_gcloud", return_value=json.dumps(addrs)):
            costs, errors = check_static_ips("proj")
        assert len(costs) == 1
        assert "ok" in costs[0]
        assert len(errors) == 1


class TestPSC転送ルールのチェック:
    def test_転送ルールは稼働中としてコスト表示(self):
        rules = [{"name": "rule-a"}, {"name": "rule-b"}]
        with patch("resources.run_gcloud", return_value=json.dumps(rules)):
            costs, errors = check_psc("proj")
        assert len(costs) == 2
        assert "rule-a" in costs[0]
        assert errors == []

    def test_転送ルールが0件なら空で返る(self):
        with patch("resources.run_gcloud", return_value="[]"):
            costs, errors = check_psc("proj")
        assert costs == []
        assert errors == []

    def test_nameフィールドが欠ければsilentにunknown表示せずエラー(self):
        with patch("resources.run_gcloud", return_value=json.dumps([{"target": "x"}])):
            costs, errors = check_psc("proj")
        assert costs == []
        assert len(errors) == 1


# 経路の実行/スキップを「どの関数を呼んだか」ではなく観測可能な戻り値で検証するためのマーカー。
_CLOUDSQL_COST = "marker: cloudsql"
_STATIC_IPS_COST = "marker: static_ips"
_PSC_COST = "marker: psc"


class Testプロジェクト単位の総合チェック:
    @pytest.fixture(autouse=True)
    def _patches(self):
        with patch("resources.check_cloudsql", return_value=([_CLOUDSQL_COST], [])) as cloudsql, \
             patch("resources.check_static_ips", return_value=([_STATIC_IPS_COST], [])) as static_ips, \
             patch("resources.check_psc", return_value=([_PSC_COST], [])):
            self.cloudsql = cloudsql
            self.static_ips = static_ips
            yield

    def test_全てのチェックが実行されコストが揃って返る(self):
        costs, _ = check_environment("proj")
        assert _CLOUDSQL_COST in costs
        assert _STATIC_IPS_COST in costs
        assert _PSC_COST in costs

    def test_各チェックのcostsとerrorsが集約されて返る(self):
        self.cloudsql.return_value = (["Cloud SQL 稼働中"], [])
        self.static_ips.return_value = (["予約済み外部 IP 1 件"], ["外部 IP チェック失敗"])
        costs, errors = check_environment("proj")
        assert "Cloud SQL 稼働中" in costs
        assert "予約済み外部 IP 1 件" in costs
        assert "外部 IP チェック失敗" in errors


class Test外部コマンド実行ラッパー:
    """_is_not_found の判定結果を「NotFound → silent skip / それ以外 → 例外で上げる」の分岐に
    繋げる要所。_is_not_found 単体が正確でも、ここが判定を間違った分岐に繋げたら silent
    failure になるため、分岐パスを直接固定する。
    """

    def test_exit0のときstdoutをstripして返す(self):
        with patch("resources.subprocess.run", return_value=_subprocess_result(0, stdout="  hello\n")):
            assert _run_cmd(["fake"], label="test") == "hello"

    def test_allow_not_found時のNotFound_stderrは空文字で抜ける(self):
        stderr = "ERROR: (gcloud.sql.instances.describe) NOT_FOUND: gone"
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr=stderr)):
            assert _run_cmd(["fake"], label="test", allow_not_found=True) == ""

    def test_allow_not_found時でもpermission_deniedは例外にする(self):
        # 最重要: _is_not_found が PERMISSION_DENIED を False 判定しても、_run_cmd が
        # それを活かせないと silent failure になる。判定結果→分岐の結合を固定する。
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="ERROR: permission denied")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="test", allow_not_found=True)

    def test_allow_not_found時でもネットワークエラーは例外にする(self):
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="connection refused")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="test", allow_not_found=True)

    def test_allow_not_foundがFalseならNotFound_stderrでも例外にする(self):
        # 呼び出し側が明示的に許可していない限り、NotFound でも未定義の状態としてエラーで止める。
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="NOT_FOUND")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="test", allow_not_found=False)

    def test_stderr空はNotFound判定できず例外にする(self):
        # `if allow_not_found and stderr and _is_not_found(stderr):` の中間 `and stderr` の
        # 存在意義。空 stderr を NotFound と誤判定して silent に抜ける経路を作らない。
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="", stdout="")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="test", allow_not_found=True)

    def test_失敗時にformat_cmd_failureの詳細がprintされる(self, capsys):
        # Slack に行くのは短い CommandError だけ。詳細はこの print 経由でしか残らないため、
        # ログへの到達を固定しないと format_cmd_failure 単体テストが無意味になる。
        with patch("resources.subprocess.run", return_value=_subprocess_result(1, stderr="actual failure")):
            with pytest.raises(CommandError):
                _run_cmd(["fake"], label="mylabel")
        captured = capsys.readouterr()
        assert "mylabel" in captured.out
        assert "actual failure" in captured.out

    def test_CommandErrorにlabelとexit_codeが含まれる(self):
        # Slack には短いメッセージだけ届くため、Actions ログ検索の手がかりとして
        # label と exit code の両方が必須。
        with patch("resources.subprocess.run", return_value=_subprocess_result(42, stderr="fail")):
            with pytest.raises(CommandError, match=r"mylabel 実行失敗 \(exit 42\)"):
                _run_cmd(["fake"], label="mylabel")

    def test_JSON出力ラッパはgcloud引数の末尾にJSON形式を指定して実行する(self):
        with patch("resources.subprocess.run", return_value=_subprocess_result(0, stdout="[]")) as run:
            run_gcloud("sql", "instances", "list")
        assert run.call_args.args[0] == ["gcloud", "sql", "instances", "list", "--format=json"]

    def test_テキスト出力ラッパはgcloud引数をそのまま実行する(self):
        with patch("resources.subprocess.run", return_value=_subprocess_result(0, stdout="RUNNABLE")) as run:
            run_gcloud_value("sql", "instances", "describe", "TST")
        assert run.call_args.args[0] == ["gcloud", "sql", "instances", "describe", "TST"]


class Test監視対象環境の読み込み:
    def test_ENVIRONMENTS_JSONが設定されていればYAMLより優先される(self):
        env = {"ENVIRONMENTS_JSON": '{"dev": "proj-dev", "stg": "proj-stg"}'}
        with patch.dict(os.environ, env, clear=True):
            result = load_environments()
        assert result == {"dev": "proj-dev", "stg": "proj-stg"}

    def test_不正なJSONは例外で落としsilentにYAMLへ逃げない(self):
        # silent に YAML にフォールバックすると設定ミスが露見せず、意図と違う環境を
        # 監視し続ける事故になる。
        env = {"ENVIRONMENTS_JSON": "not-json"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(json.JSONDecodeError):
                load_environments()

    def test_ENVIRONMENTS_JSON未設定時はYAMLを読む(self, tmp_path):
        yaml_file = tmp_path / "environments.yaml"
        yaml_file.write_text("dev: proj-dev\nstg: proj-stg\n")
        with patch.dict(os.environ, {}, clear=True), \
             patch("check.ENVIRONMENTS_YAML", yaml_file):
            result = load_environments()
        assert result == {"dev": "proj-dev", "stg": "proj-stg"}


class TestSlack通知:
    def test_渡したメッセージをtextペイロードとしてwebhookへ送る(self):
        with patch("check.urllib.request.urlopen") as urlopen:
            notify_slack("https://webhook", "コスト警告メッセージ")
        sent_request = urlopen.call_args.args[0]
        assert sent_request.full_url == "https://webhook"
        assert json.loads(sent_request.data.decode()) == {"text": "コスト警告メッセージ"}

    def test_Slack通知失敗はexit1でプロセス失敗にする(self):
        # 通知が届かないと誰もエラーに気付けないため、Actions Job を失敗させて
        # ジョブ失敗メール/通知で気付かせる二重化。
        with patch("check.urllib.request.urlopen", side_effect=Exception("boom")):
            with pytest.raises(SystemExit) as exc:
                notify_slack("https://webhook", "msg")
        assert exc.value.code == 1


class Test各チェックのCommandErrorメッセージ:
    """既存テストは run_gcloud の return_value を差し替えていて、CommandError 例外パスを
    通っていない。呼び出し側の except 節の存在と、積まれる文字列の質を直接検証する。
    """

    def test_check_cloudsqlのCommandErrorは読めるメッセージをerrorsに積む(self):
        with patch("resources.run_gcloud_value", side_effect=CommandError("gcloud 実行失敗 (exit 1)")):
            costs, errors = check_cloudsql("proj")
        assert costs == []
        assert len(errors) == 1
        assert "Cloud SQL" in errors[0]
        assert "gcloud 実行失敗" in errors[0]

    def test_check_static_ipsのCommandErrorは読めるメッセージをerrorsに積む(self):
        with patch("resources.run_gcloud", side_effect=CommandError("gcloud 実行失敗 (exit 1)")):
            costs, errors = check_static_ips("proj")
        assert costs == []
        assert len(errors) == 1
        assert "外部 IP" in errors[0]

    def test_check_pscのCommandErrorは読めるメッセージをerrorsに積む(self):
        with patch("resources.run_gcloud", side_effect=CommandError("gcloud 実行失敗 (exit 1)")):
            costs, errors = check_psc("proj")
        assert costs == []
        assert len(errors) == 1
        assert "PSC" in errors[0]


class Testエラーヘッダの組み立て:
    """Slack に載る短いエラー文（「gcloud 実行失敗 (exit 1)」等）だけでは原因追跡が
    不可能なため、Actions URL か「URL 取得不可」の明示を必ず含めることで、ユーザーが
    どこを見ればいいか迷わないようにする。
    """

    def test_Actions_URLが取れる時はSlackリンク形式で埋め込む(self):
        header = _build_error_header(
            "2026-04-17", "https://github.com/org/repo/actions/runs/12345",
        )
        assert "https://github.com/org/repo/actions/runs/12345" in header
        assert "<https://github.com/org/repo/actions/runs/12345|ログ>" in header

    def test_URL取得不可の時も動線なしを明示して調査起点を与える(self):
        # silent に URL を省略するとユーザーは「なぜログ無いのか」分からず調査を諦める。
        # 必ず「取得不可」と理由を載せて問い合わせの起点になる文言にする。
        header = _build_error_header("2026-04-17", "")
        assert "ログ URL 取得不可" in header
        # ユーザーが見るべき代替動線（stdout）を明示
        assert "stdout" in header

    def test_URL有無に関わらず日付とエラーマーカーは常に含まれる(self):
        with_url = _build_error_header("2026-04-17", "https://x/y")
        without_url = _build_error_header("2026-04-17", "")
        for h in [with_url, without_url]:
            assert "2026-04-17" in h
            assert ":x:" in h
            assert "チェックエラー" in h


def _run_cost_main(env_results: dict[str, tuple[list[str], list[str]]]) -> dict:
    """main() を環境ごとの (costs, errors) 指定で実行し、Slack へ渡った message を捕捉する。

    Args:
        env_results: 環境名 -> (costs, errors) の辞書。ENVIRONMENTS_JSON もこのキー
            集合から組み立てる。

    Returns:
        {"called": bool, "call_count": int, "message": str, "exit_code": int | None}
    """
    captured: dict = {"called": False, "call_count": 0, "message": ""}

    def fake_notify(webhook_url: str, message: str) -> None:
        captured["called"] = True
        captured["call_count"] += 1
        captured["message"] = message

    projects = {f"proj-{env}": result for env, result in env_results.items()}

    def fake_check_environment(project: str):
        return projects[project]

    environments_json = json.dumps({env: f"proj-{env}" for env in env_results})
    env_vars = {"SLACK_WEBHOOK_URL": "https://webhook", "ENVIRONMENTS_JSON": environments_json}

    exit_code = None
    with patch.dict(os.environ, env_vars, clear=True), \
         patch("check.check_environment", side_effect=fake_check_environment), \
         patch("check.notify_slack", side_effect=fake_notify):
        try:
            check.main()
        except SystemExit as e:
            exit_code = e.code
    captured["exit_code"] = exit_code
    return captured


class Testコスト確認mainの通知:
    """個々のチェック関数は戻り値までしか見ないため、main() の集約・メッセージ組み立てで
    内容が欠落しても検知できない。ユーザーが異常に気付ける唯一の経路である Slack payload を
    起点に保証する。
    """

    def test_SLACK_WEBHOOK_URL未設定のときexit_1で落ちてSlackへは送らない(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("check.notify_slack") as notify:
            with pytest.raises(SystemExit) as exc:
                check.main()
        assert exc.value.code == 1
        notify.assert_not_called()

    def test_監視対象の環境が0件のときexit_1で落ちる(self):
        env_vars = {"SLACK_WEBHOOK_URL": "https://webhook", "ENVIRONMENTS_JSON": "{}"}
        with patch.dict(os.environ, env_vars, clear=True):
            with pytest.raises(SystemExit) as exc:
                check.main()
        assert exc.value.code == 1

    def test_コストもエラーも無いとき稼働中リソースなしの確認通知を送り正常終了する(self):
        result = _run_cost_main({"dev": ([], [])})
        assert result["called"] is True
        assert ":white_check_mark:" in result["message"]
        assert "稼働中リソースなし" in result["message"]
        assert result["exit_code"] is None
        assert result["call_count"] == 1

    def test_コストがあるとき環境見出しの下に各リソースが箇条書きで載る(self):
        result = _run_cost_main({"dev": (["Cloud SQL TST"], [])})
        assert "コスト警告" in result["message"]
        assert "*dev*" in result["message"]
        assert "  • Cloud SQL TST" in result["message"]
        assert result["exit_code"] is None

    def test_エラーがあるときエラーヘッダ付きで通知しexit_1になる(self):
        result = _run_cost_main({"dev": ([], ["外部 IP TST チェック失敗"])})
        assert "チェックエラー" in result["message"]
        assert "  • 外部 IP TST チェック失敗" in result["message"]
        assert result["exit_code"] == 1

    def test_環境が複数のとき環境ごとのコストが1通にまとまる(self):
        result = _run_cost_main({
            "dev": (["Cloud SQL TST"], []),
            "stg": (["予約済み外部 IP TST"], []),
        })
        assert "*dev*" in result["message"]
        assert "*stg*" in result["message"]
        assert "Cloud SQL TST" in result["message"]
        assert "予約済み外部 IP TST" in result["message"]

    def test_コストとエラーが混在するとき警告とエラーヘッダの両方が1通に載りexit_1になる(self):
        result = _run_cost_main({
            "dev": (["Cloud SQL TST"], []),
            "stg": ([], ["外部 IP TST チェック失敗"]),
        })
        assert "コスト警告" in result["message"]
        assert "チェックエラー" in result["message"]
        assert result["exit_code"] == 1
