#!/usr/bin/env python3
import json
from unittest.mock import MagicMock, patch

import pytest
import check
from check import (
    SLACK_TEXT_LIMIT,
    PlanParseError,
    _diff_paths,
    _strip_init_noise,
    clone_repo,
    format_action_label,
    format_summary,
    load_targets,
    notify_slack,
    parse_plan_json,
    run_terraform_plan,
)


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _resource_change(
    address: str,
    type_: str,
    actions: list[str],
    before: dict | None,
    after: dict | None,
) -> dict:
    """plan JSON の resource_changes 要素を組み立てる。"""
    return {
        "address": address,
        "type": type_,
        "change": {"actions": actions, "before": before, "after": after},
    }


def _plan_json(*resource_changes: dict) -> str:
    """plan JSON 文字列を組み立てる。"""
    return json.dumps({"resource_changes": list(resource_changes)})


CLOUDSQL = "google_sql_database_instance"
ACTIVATION_POLICY_SUPPRESS = [{"type": CLOUDSQL, "attribute": "settings[0].activation_policy"}]


class Testinitノイズの除去:
    def test_Initializing系のボイラープレート行が削られる(self):
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

    def test_providerインストール系の行が削られる(self):
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

    def test_エラー本文はそのまま残る(self):
        out = "Error: Failed to query available provider packages\n\nCould not retrieve the list of available versions"
        cleaned = _strip_init_noise(out)
        assert "Failed to query available provider packages" in cleaned
        assert "Could not retrieve the list of available versions" in cleaned

    def test_空行は削られる(self):
        out = "line1\n\n\nline2\n"
        cleaned = _strip_init_noise(out)
        assert cleaned == "line1\nline2"

    def test_空入力は空文字列を返す(self):
        assert _strip_init_noise("") == ""


class Test差分属性パスの列挙:
    def test_dict配下のスカラ差分はkeyパスで検出される(self):
        paths = _diff_paths({"tier": "old"}, {"tier": "new"})
        assert paths == ["tier"]

    def test_dictのネストはドット区切りで連結される(self):
        paths = _diff_paths({"a": {"b": 1}}, {"a": {"b": 2}})
        assert paths == ["a.b"]

    def test_list要素の差分は角括弧インデックスで示される(self):
        paths = _diff_paths(
            {"settings": [{"activation_policy": "ALWAYS"}]},
            {"settings": [{"activation_policy": "NEVER"}]},
        )
        assert paths == ["settings[0].activation_policy"]

    def test_完全一致なら空リストになる(self):
        assert _diff_paths({"a": 1}, {"a": 1}) == []

    def test_片側だけ存在するキーは差分として検出される(self):
        # silent にスキップすると「drift を拾い損ねる」ため。
        paths = _diff_paths({"a": 1, "b": 2}, {"a": 1})
        assert paths == ["b"]

    def test_複数箇所の差分は全部列挙される(self):
        # 重複抑止の判定材料として完全性が要る。
        paths = _diff_paths(
            {"tier": "old", "flag": False},
            {"tier": "new", "flag": True},
        )
        assert set(paths) == {"tier", "flag"}


class Testplan_JSONのsuppress分離:
    def test_no_opのリソースはvisibleにもsuppressedにも積まれない(self):
        plan = _plan_json(
            _resource_change("google_foo.bar", "google_foo", ["no-op"], {}, {}),
        )
        visible, suppressed = parse_plan_json(plan, [])
        assert visible == []
        assert suppressed == []

    def test_readアクションのdataソースはvisibleにもsuppressedにも積まれない(self):
        # terraform は data source の plan 時 refresh が確定しないとき (依存する managed
        # resource が同 plan で変更予定 / 引数に unknown after apply が含まれる) に ["read"] を
        # 発行する。トリガとなる managed resource の変更は同じ plan に必ず併載されるため、
        # ["read"] を skip しても drift 信号は失われない。
        plan = _plan_json(
            _resource_change("data.google_sql_database_instance.target", "google_sql_database_instance", ["read"], None, None),
        )
        visible, suppressed = parse_plan_json(plan, [])
        assert visible == []
        assert suppressed == []

    def test_readと同planのトリガresourceはvisibleに残り通知される(self):
        plan = _plan_json(
            _resource_change("data.google_sql_database_instance.target", "google_sql_database_instance", ["read"], None, None),
            _resource_change("module.psc_cloudsql.google_project_service.sqladmin", "google_project_service", ["update"], {"disable_on_destroy": True}, {"disable_on_destroy": False}),
        )
        visible, suppressed = parse_plan_json(plan, [])
        assert len(visible) == 1
        assert visible[0]["type"] == "google_project_service"
        assert suppressed == []

    def test_ルール一致属性だけ変わったupdateはsuppressedへ(self):
        # 「dev/stg で nightly-shutdown が動いた後の日常ノイズ」を拾わない核心仕様。
        plan = _plan_json(_resource_change(
            "module.database.google_sql_database_instance.main",
            CLOUDSQL,
            ["update"],
            {"settings": [{"activation_policy": "ALWAYS", "tier": "db-g1-small"}]},
            {"settings": [{"activation_policy": "NEVER", "tier": "db-g1-small"}]},
        ))
        visible, suppressed = parse_plan_json(plan, ACTIVATION_POLICY_SUPPRESS)
        assert visible == []
        assert len(suppressed) == 1

    def test_ルール対象外の属性が1つでも変われば_visibleに残す(self):
        # tier 変更のような意図しない変更を activation_policy と同時に起こしたときに silent に
        # しないため。リソース丸ごと通知することで人に調査させる。
        plan = _plan_json(_resource_change(
            "module.database.google_sql_database_instance.main",
            CLOUDSQL,
            ["update"],
            {"settings": [{"activation_policy": "ALWAYS", "tier": "db-g1-small"}]},
            {"settings": [{"activation_policy": "NEVER", "tier": "db-n1-standard-1"}]},
        ))
        visible, suppressed = parse_plan_json(plan, ACTIVATION_POLICY_SUPPRESS)
        assert len(visible) == 1
        assert suppressed == []

    def test_suppressルールが空なら_prod相当で全てvisible(self):
        plan = _plan_json(_resource_change(
            "module.database.google_sql_database_instance.main",
            CLOUDSQL,
            ["update"],
            {"settings": [{"activation_policy": "ALWAYS"}]},
            {"settings": [{"activation_policy": "NEVER"}]},
        ))
        visible, suppressed = parse_plan_json(plan, [])
        assert len(visible) == 1
        assert suppressed == []

    def test_createは構造的変更なのでsuppress対象外(self):
        # 新規リソース追加を「属性一致」を理由に抑止するのは明らかに危険。
        plan = _plan_json(_resource_change(
            "google_sql_database_instance.main",
            CLOUDSQL,
            ["create"],
            None,
            {"settings": [{"activation_policy": "ALWAYS"}]},
        ))
        visible, suppressed = parse_plan_json(plan, ACTIVATION_POLICY_SUPPRESS)
        assert len(visible) == 1
        assert suppressed == []

    def test_replaceは構造的変更なのでsuppress対象外(self):
        plan = _plan_json(_resource_change(
            "google_sql_database_instance.main",
            CLOUDSQL,
            ["delete", "create"],
            {"settings": [{"activation_policy": "ALWAYS"}]},
            {"settings": [{"activation_policy": "ALWAYS"}]},
        ))
        visible, suppressed = parse_plan_json(plan, ACTIVATION_POLICY_SUPPRESS)
        assert len(visible) == 1
        assert suppressed == []

    def test_ルールのtypeが違うリソースは抑止対象外(self):
        plan = _plan_json(_resource_change(
            "google_storage_bucket.a",
            "google_storage_bucket",
            ["update"],
            {"settings": [{"activation_policy": "ALWAYS"}]},
            {"settings": [{"activation_policy": "NEVER"}]},
        ))
        visible, suppressed = parse_plan_json(plan, ACTIVATION_POLICY_SUPPRESS)
        assert len(visible) == 1
        assert suppressed == []

    def test_同一planでsuppressedとvisibleが正しく振り分けられる(self):
        plan = _plan_json(
            _resource_change(
                "google_sql_database_instance.main",
                CLOUDSQL,
                ["update"],
                {"settings": [{"activation_policy": "ALWAYS"}]},
                {"settings": [{"activation_policy": "NEVER"}]},
            ),
            _resource_change(
                "google_storage_bucket.a",
                "google_storage_bucket",
                ["update"],
                {"versioning": False},
                {"versioning": True},
            ),
        )
        visible, suppressed = parse_plan_json(plan, ACTIVATION_POLICY_SUPPRESS)
        assert len(visible) == 1
        assert visible[0]["type"] == "google_storage_bucket"
        assert len(suppressed) == 1
        assert suppressed[0]["type"] == CLOUDSQL

    def test_不正なJSONはPlanParseErrorになる(self):
        # silent に [] 扱いすると「差分ゼロ」と誤通知されるため。
        with pytest.raises(PlanParseError):
            parse_plan_json("not a json", [])


class Testサマリの組み立て:
    def test_Plan行の追加変更削除の件数を集計する(self):
        changes = [
            _resource_change("a", "t", ["create"], None, {}),
            _resource_change("b", "t", ["update"], {}, {}),
            _resource_change("c", "t", ["delete"], {}, None),
        ]
        summary = format_summary(changes)
        assert "Plan: 1 to add, 1 to change, 1 to destroy." in summary

    def test_replaceはaddとdestroyに1ずつ積まれる(self):
        # terraform plan 本家のテキスト集計と同じ慣習に合わせる。
        changes = [_resource_change("a", "t", ["delete", "create"], {}, {})]
        summary = format_summary(changes)
        assert "Plan: 1 to add, 0 to change, 1 to destroy." in summary

    def test_アドレスとアクションラベルがwill_be形式で出る(self):
        changes = [
            _resource_change("google_storage_bucket.a", "google_storage_bucket", ["create"], None, {}),
        ]
        summary = format_summary(changes)
        assert "google_storage_bucket.a will be created" in summary

    def test_11件以上は10件までと他N件にまとめる(self):
        # Slack 通知の読みやすさ確保と文字数制限への対応。
        changes = [
            _resource_change(f"r.{i}", "t", ["create"], None, {})
            for i in range(15)
        ]
        summary = format_summary(changes)
        assert "r.0 will be created" in summary
        assert "r.9 will be created" in summary
        assert "r.10 will be created" not in summary
        assert "他 5 リソース" in summary

    def test_リソースが10件ちょうどのとき全件が列挙され集約行は付かない(self):
        changes = [
            _resource_change(f"r.{i}", "t", ["create"], None, {})
            for i in range(10)
        ]
        summary = format_summary(changes)
        assert "r.9 will be created" in summary
        assert "他" not in summary

    def test_リソースが11件のとき先頭10件と他1リソースに集約される(self):
        changes = [
            _resource_change(f"r.{i}", "t", ["create"], None, {})
            for i in range(11)
        ]
        summary = format_summary(changes)
        assert "r.9 will be created" in summary
        assert "r.10" not in summary
        assert "他 1 リソース" in summary

    def test_visibleが空ならPlanParseErrorになる(self):
        # 「visible ゼロ」は上位で「suppress で全部吸収 → no drift」として扱う分岐に倒すべきで、
        # summary に進ませるのは呼び出しミス。silent に空文字を返さない。
        with pytest.raises(PlanParseError):
            format_summary([])

    def test_アクションが既知の5パターン外のときPlanParseErrorになる(self):
        with pytest.raises(PlanParseError, match="未知の actions"):
            format_action_label(["forget"])

    def test_createとdeleteの順で並ぶreplaceもreplacedと表示される(self):
        changes = [_resource_change("a", "t", ["create", "delete"], {}, {})]
        summary = format_summary(changes)
        assert "will be replaced" in summary
        assert "Plan: 1 to add, 0 to change, 1 to destroy." in summary


class Testtargets_yamlの読み込み:
    def test_targets_yamlの内容をlistとしてロードする(self, tmp_path):
        yaml_file = tmp_path / "targets.yaml"
        yaml_file.write_text("- repo: a\n  environments: []\n")
        with patch("check.TARGETS_YAML", yaml_file):
            result = load_targets()
        assert result == [{"repo": "a", "environments": []}]

    def test_targets_yamlが無ければFileNotFoundErrorを投げる(self, tmp_path):
        # silent に [] を返すと「監視対象ゼロ = 全環境クリア」と誤認される事故を招く。
        missing = tmp_path / "nope.yaml"
        with patch("check.TARGETS_YAML", missing):
            with pytest.raises(FileNotFoundError):
                load_targets()


class Testリポジトリのclone:
    def test_clone成功時はdestパスを返す(self):
        with patch("check.run", return_value=_proc(0)):
            result = clone_repo("repo-x", "token")
        assert result is not None
        assert "repo-x" in result

    def test_clone失敗時はNoneを返し呼び出し側でerrorsに積ませる(self):
        # 例外で落とさず None で伝えるのは、他のリポジトリの plan を続けるため。
        with patch("check.run", return_value=_proc(1, stderr="auth failed")):
            assert clone_repo("repo-x", "token") is None

    def test_取得元URLにtokenを含めない(self):
        with patch("check.run", return_value=_proc(0)) as fake_run:
            clone_repo("repo-x", "TSTTOKEN")
        args = fake_run.call_args.args[0]
        assert not any("TSTTOKEN" in arg for arg in args)
        assert "https://github.com/kenyamaneko/repo-x.git" in args

    def test_gitの設定として環境変数でtokenを渡す(self):
        with patch("check.run", return_value=_proc(0)) as fake_run:
            clone_repo("repo-x", "TSTTOKEN")
        env = fake_run.call_args.kwargs["env"]
        assert env["GIT_CONFIG_COUNT"] == "1"
        assert env["GIT_CONFIG_KEY_0"] == (
            "url.https://x-access-token:TSTTOKEN@github.com/.insteadOf"
        )
        assert env["GIT_CONFIG_VALUE_0"] == "https://github.com/"

    def test_呼び出し元の環境が既にgit設定を持つときSystemExitで中断する(self, monkeypatch):
        monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
        with patch("check.run", return_value=_proc(0)):
            with pytest.raises(SystemExit, match="GIT_CONFIG_COUNT is already set"):
                clone_repo("repo-x", "TSTTOKEN")

    def test_失敗詳細がリポ名付きでstderrに出る(self, capsys):
        # リポ名を含めないと、どのリポで何が失敗したか Actions ログから追えなくなる。
        with patch("check.run", return_value=_proc(1, stderr="auth failed")):
            clone_repo("repo-x", "token")
        captured = capsys.readouterr()
        assert "auth failed" in captured.err
        assert "repo-x" in captured.err


class Testterraform_planの3段パイプ:
    def test_init失敗ならplanを実行せず1と詳細を返す(self):
        with patch("check.run", return_value=_proc(1, stderr="init failed")):
            code, output = run_terraform_plan("/work")
        assert code == 1
        assert "init failed" in output

    def test_init失敗時はplanを叩かない(self):
        with patch("check.run", side_effect=[_proc(1, stderr="init failed")]) as run_mock:
            run_terraform_plan("/work")
        assert run_mock.call_count == 1

    def test_plan_exit0なら0と空を返しshowは呼ばない(self):
        with patch("check.run", side_effect=[_proc(0), _proc(0)]) as run_mock:
            code, output = run_terraform_plan("/work")
        assert code == 0
        assert output == ""
        assert run_mock.call_count == 2

    def test_plan_exit2ならshow_jsonを実行し2とJSONを返す(self):
        # 上位 (parse_plan_json) が属性単位で suppress 判定するため JSON が必要。
        plan_json = _plan_json(_resource_change("r.a", "t", ["update"], {"x": 1}, {"x": 2}))
        with patch("check.run", side_effect=[
            _proc(0), _proc(2), _proc(0, stdout=plan_json),
        ]):
            code, output = run_terraform_plan("/work")
        assert code == 2
        assert output == plan_json

    def test_plan_exitが非0非2なら1でdriftとエラーを混同しない(self):
        with patch("check.run", side_effect=[_proc(0), _proc(1, stderr="provider error")]):
            code, _ = run_terraform_plan("/work")
        assert code == 1

    def test_drift時でもshow_jsonが失敗したらerror扱いにする(self):
        # JSON が取れなければ上位で suppress 判定できず「内容不明な drift」と同じ構造的問題に
        # なるため error として仕分ける。
        with patch("check.run", side_effect=[
            _proc(0), _proc(2), _proc(1, stderr="show failed"),
        ]):
            code, output = run_terraform_plan("/work")
        assert code == 1
        assert "show failed" in output

    def test_error時はstderrを優先して詳細に選ぶ(self):
        with patch("check.run", side_effect=[
            _proc(0),
            _proc(1, stdout="stdout content", stderr="stderr detail"),
        ]):
            _, output = run_terraform_plan("/work")
        assert "stderr detail" in output


class TestSlack通知のtruncate:
    def _capture_payload(self):
        captured = {}
        def _urlopen(req):
            captured["data"] = req.data.decode()
            return MagicMock()
        return captured, _urlopen

    def test_渡したメッセージをtextペイロードとしてwebhookへ送る(self):
        with patch("check.urllib.request.urlopen") as urlopen:
            notify_slack("https://webhook", "hello")
        sent_request = urlopen.call_args.args[0]
        assert sent_request.full_url == "https://webhook"
        assert json.loads(sent_request.data.decode()) == {"text": "hello"}

    def test_長大メッセージでもSlack_API制限内に収まりtruncateマーカーが付く(self):
        # 検証すべきは「制限を超えたときに Slack が受け付ける形に収まるか」であり、
        # 内部定数 SLACK_TEXT_LIMIT の値そのものではない（定数は実装詳細）。
        captured, urlopen = self._capture_payload()
        with patch("check.urllib.request.urlopen", side_effect=urlopen):
            notify_slack("https://webhook", "x" * 10000)
        payload = json.loads(captured["data"])
        assert "…(truncated)" in payload["text"]
        assert len(payload["text"]) < 4000

    def test_文字数が上限ちょうどのとき切り詰めない(self):
        captured, urlopen = self._capture_payload()
        with patch("check.urllib.request.urlopen", side_effect=urlopen):
            notify_slack("https://webhook", "x" * SLACK_TEXT_LIMIT)
        payload = json.loads(captured["data"])
        assert "truncated" not in payload["text"]

    def test_urlopen例外時はexit1でActions_Jobを失敗させる(self):
        # Slack への到達が唯一の可視化経路なので、届かなかった時はプロセス終了コードで
        # 二重に気付かせる。
        with patch("check.urllib.request.urlopen", side_effect=Exception("boom")):
            with pytest.raises(SystemExit) as exc:
                notify_slack("https://webhook", "x")
        assert exc.value.code == 1


def _run_main_with_plan_result(
    plan_exit_code: int,
    plan_output: str,
    suppress: list[dict] | None = None,
    *,
    env_vars: dict | None = None,
    clone_result: str | None = "/tmp/r",
    is_dir: bool = True,
) -> dict:
    """main() を 1 target で実行し、notify_slack が呼ばれたかと渡された message を返す。

    Args:
        plan_exit_code: run_terraform_plan が返す exit code。
        plan_output: run_terraform_plan が返す output。
        suppress: targets.yaml の suppress ルール。None なら未指定。
        env_vars: 環境変数の上書き。None なら GITHUB_TOKEN / SLACK_WEBHOOK_URL が
            揃った状態で実行する。
        clone_result: clone_repo の戻り値。None なら clone 失敗を模す。
        is_dir: os.path.isdir の戻り値。

    Returns:
      {"called": bool, "message": str, "exit_code": int | None}
    """
    captured: dict = {"called": False, "message": ""}

    def fake_notify(webhook_url: str, message: str) -> None:
        captured["called"] = True
        captured["message"] = message

    if env_vars is None:
        env_vars = {"GITHUB_TOKEN": "t", "SLACK_WEBHOOK_URL": "https://webhook"}
    env_def = {"name": "e", "path": "p"}
    if suppress is not None:
        env_def["suppress"] = suppress
    targets = [{"repo": "r", "environments": [env_def]}]

    exit_code = None
    with patch.dict("os.environ", env_vars, clear=True), \
         patch("check.load_targets", return_value=targets), \
         patch("check.clone_repo", return_value=clone_result), \
         patch("check.os.makedirs"), \
         patch("check.os.path.isdir", return_value=is_dir), \
         patch("check.run_terraform_plan", return_value=(plan_exit_code, plan_output)), \
         patch("check.notify_slack", side_effect=fake_notify):
        try:
            check.main()
        except SystemExit as e:
            exit_code = e.code
    captured["exit_code"] = exit_code
    return captured


def _run_main_with_targets(
    targets: list[dict],
    plan_results: list[tuple[int, str]],
) -> dict:
    """main() を複数 target・複数 env で実行し、Slack へ渡った message を返す。

    Args:
        targets: load_targets が返す target 定義のリスト。
        plan_results: run_terraform_plan が target/env の走査順で返す (exit_code, output) の列。

    Returns:
      {"called": bool, "message": str}
    """
    captured: dict = {"called": False, "message": ""}

    def fake_notify(webhook_url: str, message: str) -> None:
        captured["called"] = True
        captured["message"] = message

    env_vars = {"GITHUB_TOKEN": "t", "SLACK_WEBHOOK_URL": "https://webhook"}

    with patch.dict("os.environ", env_vars, clear=True), \
         patch("check.load_targets", return_value=targets), \
         patch("check.clone_repo", return_value="/tmp/r"), \
         patch("check.os.makedirs"), \
         patch("check.os.path.isdir", return_value=True), \
         patch("check.run_terraform_plan", side_effect=plan_results), \
         patch("check.notify_slack", side_effect=fake_notify):
        check.main()
    return captured


class Testmainのエラー伝播:
    def test_plan実行エラーの詳細がSlack_payloadに載る(self):
        result = _run_main_with_plan_result(1, "provider auth failed: 401")
        assert result["called"] is True
        assert "plan 実行エラー" in result["message"]
        assert "r/e" in result["message"]
        assert "provider auth failed: 401" in result["message"]

    def test_drift検出時はplan_JSONをparseしたサマリがSlack_payloadに載る(self):
        plan_json = _plan_json(
            _resource_change("google_storage_bucket.a", "google_storage_bucket", ["create"], None, {}),
        )
        result = _run_main_with_plan_result(2, plan_json)
        assert result["called"] is True
        assert "差分を検出" in result["message"]
        assert "r/e" in result["message"]
        assert "Plan: 1 to add, 0 to change, 0 to destroy." in result["message"]
        assert "google_storage_bucket.a will be created" in result["message"]

    def test_GITHUB_TOKEN未設定のときexit_1で落ちてSlackへは送らない(self):
        result = _run_main_with_plan_result(0, "", env_vars={"SLACK_WEBHOOK_URL": "https://webhook"})
        assert result["called"] is False
        assert result["exit_code"] == 1

    def test_SLACK_WEBHOOK_URL未設定のときexit_1で落ちる(self):
        result = _run_main_with_plan_result(0, "", env_vars={"GITHUB_TOKEN": "t"})
        assert result["exit_code"] == 1

    def test_リポジトリのcloneに失敗したときclone_failedがplan実行エラー通知に載る(self):
        result = _run_main_with_plan_result(0, "", clone_result=None)
        assert "plan 実行エラー" in result["message"]
        assert "r/-" in result["message"]
        assert "clone failed" in result["message"]

    def test_環境ディレクトリが無いときdirectory_not_foundがplan実行エラー通知に載る(self):
        result = _run_main_with_plan_result(0, "", is_dir=False)
        assert "r/e" in result["message"]
        assert "directory not found" in result["message"]

    def test_drift検出後のplan_JSONが壊れているときパース失敗がplan実行エラー通知に載る(self):
        result = _run_main_with_plan_result(2, "not a json")
        assert "plan JSON のパース失敗" in result["message"]

    def test_plan未知アクションのときsummary組立失敗がplan実行エラー通知に載る(self):
        plan_json = _plan_json(
            _resource_change("google_storage_bucket.a", "google_storage_bucket", ["forget"], {}, {}),
        )
        result = _run_main_with_plan_result(2, plan_json)
        assert "summary 組立失敗" in result["message"]
        assert "未知の actions" in result["message"]


class Testmainのsuppress適用:
    def test_dev相当でactivation_policy単独差分は差分なし通知になる(self):
        plan_json = _plan_json(_resource_change(
            "module.database.google_sql_database_instance.main",
            CLOUDSQL,
            ["update"],
            {"settings": [{"activation_policy": "ALWAYS"}]},
            {"settings": [{"activation_policy": "NEVER"}]},
        ))
        result = _run_main_with_plan_result(2, plan_json, suppress=ACTIVATION_POLICY_SUPPRESS)
        assert result["called"] is True
        assert "差分なし" in result["message"]
        assert "差分を検出" not in result["message"]
        assert CLOUDSQL not in result["message"]

    def test_prod相当でactivation_policy差分があればSlack通知される(self):
        plan_json = _plan_json(_resource_change(
            "module.database.google_sql_database_instance.main",
            CLOUDSQL,
            ["update"],
            {"settings": [{"activation_policy": "ALWAYS"}]},
            {"settings": [{"activation_policy": "NEVER"}]},
        ))
        result = _run_main_with_plan_result(2, plan_json, suppress=[])
        assert result["called"] is True
        assert "差分を検出" in result["message"]
        assert CLOUDSQL in result["message"]

    def test_devでもsuppress対象外の属性が同時に変われば通知する(self):
        # 抑止が「リソース内全属性が rule 内」判定のため、1 つでも外れると visible に残す。
        plan_json = _plan_json(_resource_change(
            "module.database.google_sql_database_instance.main",
            CLOUDSQL,
            ["update"],
            {"settings": [{"activation_policy": "ALWAYS", "tier": "db-g1-small"}]},
            {"settings": [{"activation_policy": "NEVER", "tier": "db-n1-standard-1"}]},
        ))
        result = _run_main_with_plan_result(2, plan_json, suppress=ACTIVATION_POLICY_SUPPRESS)
        assert result["called"] is True
        assert "差分を検出" in result["message"]

    def test_suppressルールと無関係のリソースにdriftがあれば通知する(self):
        # dev の suppress が「Cloud SQL 以外も黙らせる」副作用を持たないことを保証する。
        plan_json = _plan_json(_resource_change(
            "google_storage_bucket.a",
            "google_storage_bucket",
            ["update"],
            {"versioning": False},
            {"versioning": True},
        ))
        result = _run_main_with_plan_result(2, plan_json, suppress=ACTIVATION_POLICY_SUPPRESS)
        assert result["called"] is True
        assert "google_storage_bucket.a" in result["message"]

    def test_plan_exit0なら毎朝の死活通知として差分なしを通知する(self):
        result = _run_main_with_plan_result(0, "", suppress=ACTIVATION_POLICY_SUPPRESS)
        assert result["called"] is True
        assert "差分なし" in result["message"]


class Testmainの複数対象集約:
    def test_2リポで片方にdrift片方にplanエラーがあるときSlack_payload1通に警告とエラーがまとまる(self):
        targets = [
            {"repo": "r1", "environments": [{"name": "e1", "path": "p"}]},
            {"repo": "r2", "environments": [{"name": "e2", "path": "p"}]},
        ]
        drift_plan = _plan_json(
            _resource_change("google_storage_bucket.a", "google_storage_bucket", ["create"], None, {}),
        )
        result = _run_main_with_targets(targets, [(2, drift_plan), (1, "provider auth failed")])
        assert "差分を検出" in result["message"]
        assert "r1/e1" in result["message"]
        assert "google_storage_bucket.a will be created" in result["message"]
        assert "plan 実行エラー" in result["message"]
        assert "r2/e2" in result["message"]
        assert "provider auth failed" in result["message"]

    def test_同一リポの複数環境でdriftが出たとき環境ごとのサマリがSlack_payloadに全て載る(self):
        targets = [
            {"repo": "r1", "environments": [{"name": "e1", "path": "p"}, {"name": "e2", "path": "p"}]},
        ]
        plan_e1 = _plan_json(
            _resource_change("google_storage_bucket.a", "google_storage_bucket", ["create"], None, {}),
        )
        plan_e2 = _plan_json(
            _resource_change("google_storage_bucket.b", "google_storage_bucket", ["create"], None, {}),
        )
        result = _run_main_with_targets(targets, [(2, plan_e1), (2, plan_e2)])
        assert "r1/e1" in result["message"]
        assert "r1/e2" in result["message"]
        assert "google_storage_bucket.a will be created" in result["message"]
        assert "google_storage_bucket.b will be created" in result["message"]
