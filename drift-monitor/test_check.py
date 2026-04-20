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
    format_summary,
    load_targets,
    notify_slack,
    parse_plan_json,
    terraform_plan,
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


class TestDiffPaths:
    """before / after JSON から差分属性パスを列挙する仕様。

    suppress 判定が属性単位なので、path 表現（dict は `.key`、list は `[i]`）が
    rule の attribute 表現と一致することを保証する。
    """

    def test_scalar_mismatch_at_top_level_key(self):
        """観点: dict 配下のスカラ差分は `.key` パスで検出される。"""
        paths = _diff_paths({"tier": "old"}, {"tier": "new"})
        assert paths == ["tier"]

    def test_nested_dict_mismatch_uses_dot_separator(self):
        """観点: dict のネストは `a.b` 形式で連結される。"""
        paths = _diff_paths({"a": {"b": 1}}, {"a": {"b": 2}})
        assert paths == ["a.b"]

    def test_list_element_mismatch_uses_bracket_index(self):
        """観点: list 要素の差分は `[i]` 形式で示される。"""
        paths = _diff_paths(
            {"settings": [{"activation_policy": "ALWAYS"}]},
            {"settings": [{"activation_policy": "NEVER"}]},
        )
        assert paths == ["settings[0].activation_policy"]

    def test_equal_values_return_empty(self):
        """観点: 完全一致なら空リスト（no-op）。"""
        assert _diff_paths({"a": 1}, {"a": 1}) == []

    def test_missing_key_is_detected_as_diff(self):
        """観点: 片側だけ存在するキーは差分として検出される。

        silent にスキップすると「drift を拾い損ねる」ため。
        """
        paths = _diff_paths({"a": 1, "b": 2}, {"a": 1})
        assert paths == ["b"]

    def test_multiple_diffs_all_listed(self):
        """観点: 複数箇所の差分は全部列挙される（重複抑止の判定材料として完全性が要る）。"""
        paths = _diff_paths(
            {"tier": "old", "flag": False},
            {"tier": "new", "flag": True},
        )
        assert set(paths) == {"tier", "flag"}


class TestParsePlanJsonSuppression:
    """plan JSON から no-op 以外のリソース変更を取り出し、suppress を分離する仕様。

    dev/stg で活きる: Cloud SQL の activation_policy 差分だけならノイズとして吸収。
    prod で活きる: suppress rule が無い env では何も抑止しない＝drift として通知。
    """

    def test_no_op_resources_are_ignored(self):
        """観点: actions=["no-op"] のリソースは visible/suppressed いずれにも積まれない。"""
        plan = _plan_json(
            _resource_change("google_foo.bar", "google_foo", ["no-op"], {}, {}),
        )
        visible, suppressed = parse_plan_json(plan, [])
        assert visible == []
        assert suppressed == []

    def test_suppressed_when_only_rule_matched_attribute_changed(self):
        """観点: dev の Cloud SQL で activation_policy だけ変わった update は suppressed へ。

        これが「dev/stg で nightly-shutdown が動いた後の日常ノイズ」を拾わない核心仕様。
        """
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

    def test_not_suppressed_when_non_rule_attribute_also_changed(self):
        """観点: suppress 対象外の属性が 1 つでも変わっていれば visible に残す。

        tier 変更のような意図しない変更を activation_policy と同時に起こしたときに
        silent にしないための仕様。リソース丸ごと通知することで人に調査させる。
        """
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

    def test_not_suppressed_when_no_suppress_rules(self):
        """観点: suppress rule が空 (prod 相当) なら全て visible。

        prod で同じ activation_policy 差分を出しても検知されることを保証する。
        """
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

    def test_create_is_never_suppressed(self):
        """観点: actions=["create"] は構造的変更なので suppress 対象外。

        新規リソース追加を「属性一致」を理由に抑止するのは明らかに危険。
        """
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

    def test_replace_is_never_suppressed(self):
        """観点: actions=["delete","create"] (replace) も構造的変更で suppress 対象外。"""
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

    def test_rule_for_different_type_does_not_apply(self):
        """観点: suppress rule の type が違うリソースは抑止対象外。"""
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

    def test_mixed_resources_split_by_suppress_rule(self):
        """観点: 同一 plan に suppressed な変更と visible な変更が混在するとき、
        それぞれ正しく振り分けられる。
        """
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

    def test_malformed_json_raises(self):
        """観点: 不正な JSON は PlanParseError で人間調査を促す。

        silent に [] 扱いすると「差分ゼロ」と誤通知されるため。
        """
        with pytest.raises(PlanParseError):
            parse_plan_json("not a json", [])


class TestFormatSummary:
    """visible_changes から Slack 通知用サマリを組み立てる仕様。"""

    def test_plan_line_counts_actions(self):
        """観点: `Plan: N to add, M to change, K to destroy` の件数が正しく集計される。"""
        changes = [
            _resource_change("a", "t", ["create"], None, {}),
            _resource_change("b", "t", ["update"], {}, {}),
            _resource_change("c", "t", ["delete"], {}, None),
        ]
        summary = format_summary(changes)
        assert "Plan: 1 to add, 1 to change, 1 to destroy." in summary

    def test_replace_counted_as_add_and_destroy(self):
        """観点: replace (delete+create) は add と destroy の両方に 1 ずつ積まれる。

        terraform plan 本家のテキスト集計と同じ慣習に合わせる。
        """
        changes = [_resource_change("a", "t", ["delete", "create"], {}, {})]
        summary = format_summary(changes)
        assert "Plan: 1 to add, 0 to change, 1 to destroy." in summary

    def test_resource_lines_render_action_label(self):
        """観点: アドレスとアクションラベルが `<addr> will be <label>` 形式で出る。"""
        changes = [
            _resource_change("google_storage_bucket.a", "google_storage_bucket", ["create"], None, {}),
        ]
        summary = format_summary(changes)
        assert "google_storage_bucket.a will be created" in summary

    def test_overflow_resources_are_collapsed(self):
        """観点: 11 件以上の変更対象は 10 件まで + 「他N件」にまとめる。

        Slack 通知の読みやすさ確保と文字数制限への対応仕様。
        """
        changes = [
            _resource_change(f"r.{i}", "t", ["create"], None, {})
            for i in range(15)
        ]
        summary = format_summary(changes)
        assert "r.0 will be created" in summary
        assert "r.9 will be created" in summary
        assert "r.10 will be created" not in summary
        assert "他 5 リソース" in summary

    def test_empty_visible_raises(self):
        """観点: visible_changes が空で呼ばれたら PlanParseError。

        「visible ゼロ」は上位で「suppress で全部吸収 → no drift」として扱う分岐に
        倒すべきで、summary に進ませるのは呼び出しミス。silent に空文字を返さない。
        """
        with pytest.raises(PlanParseError):
            format_summary([])


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
    """terraform init + plan + show -json の 3 段パイプの分岐仕様。

    -detailed-exitcode の意味:
      exit 0 = 差分なし / exit 2 = drift / exit その他 = エラー
    drift 時だけ show -json で構造化 JSON を取り、上位に渡す。
    """

    def test_init_failure_returns_1(self):
        """観点: init が失敗したら plan を実行せず (1, detail) を返す。"""
        with patch("check.run", return_value=_proc(1, stderr="init failed")):
            code, output = terraform_plan("/work")
        assert code == 1
        assert "init failed" in output

    def test_init_failure_does_not_run_plan(self):
        """観点: init 失敗時に plan を叩かない（無駄な API 呼びを防ぐ）。"""
        with patch("check.run", side_effect=[_proc(1, stderr="init failed")]) as run_mock:
            terraform_plan("/work")
        assert run_mock.call_count == 1

    def test_no_drift_returns_0_and_empty_output(self):
        """観点: plan exit 0 → (0, "")。show は呼ばない。"""
        with patch("check.run", side_effect=[_proc(0), _proc(0)]) as run_mock:
            code, output = terraform_plan("/work")
        assert code == 0
        assert output == ""
        assert run_mock.call_count == 2

    def test_drift_detected_returns_show_json(self):
        """観点: plan exit 2 → show -json を実行し (2, JSON) を返す。

        上位 (parse_plan_json) が属性単位で suppress 判定するため JSON が必要。
        """
        plan_json = _plan_json(_resource_change("r.a", "t", ["update"], {"x": 1}, {"x": 2}))
        with patch("check.run", side_effect=[
            _proc(0), _proc(2), _proc(0, stdout=plan_json),
        ]):
            code, output = terraform_plan("/work")
        assert code == 2
        assert output == plan_json

    def test_plan_error_returns_1(self):
        """観点: plan が 非0/非2 → (1, detail)。drift とエラーを混同しない。"""
        with patch("check.run", side_effect=[_proc(0), _proc(1, stderr="provider error")]):
            code, _ = terraform_plan("/work")
        assert code == 1

    def test_show_failure_returns_1(self):
        """観点: drift (plan exit 2) でも show -json が失敗したら error 扱い。

        JSON が取れなければ上位で suppress 判定できず「内容不明な drift」と同じ
        構造的問題になるため error として仕分ける。
        """
        with patch("check.run", side_effect=[
            _proc(0), _proc(2), _proc(1, stderr="show failed"),
        ]):
            code, output = terraform_plan("/work")
        assert code == 1
        assert "show failed" in output

    def test_plan_error_prefers_stderr(self):
        """観点: error 時は stderr → stdout の順で詳細を選ぶ（CLI の慣習）。"""
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


def _run_main_with_plan_result(
    plan_exit_code: int,
    plan_output: str,
    suppress: list[dict] | None = None,
) -> dict:
    """main() を 1 target で実行し、notify_slack が呼ばれたかと渡された message を返す。

    Returns:
      {"called": bool, "message": str}
    """
    captured: dict = {"called": False, "message": ""}

    def fake_notify(webhook_url: str, message: str) -> None:
        captured["called"] = True
        captured["message"] = message

    env = {"GITHUB_TOKEN": "t", "SLACK_WEBHOOK_URL": "https://webhook"}
    env_def = {"name": "e", "path": "p"}
    if suppress is not None:
        env_def["suppress"] = suppress
    targets = [{"repo": "r", "environments": [env_def]}]

    with patch.dict("os.environ", env, clear=False), \
         patch("check.load_targets", return_value=targets), \
         patch("check.clone_repo", return_value="/tmp/r"), \
         patch("check.os.makedirs"), \
         patch("check.os.path.isdir", return_value=True), \
         patch("check.terraform_plan", return_value=(plan_exit_code, plan_output)), \
         patch("check.notify_slack", side_effect=fake_notify):
        check.main()
    return captured


class TestMainErrorPropagation:
    """terraform_plan のエラーが Slack 通知まで届くことを end-to-end で検証する。

    個々のユニットテストは戻り値までしか見ないため、main() の errors 積み込みや
    メッセージ組み立てで detail が欠落しても検知できない。ユーザーが異常に気付ける
    唯一の経路である Slack payload を起点に保証する。
    """

    def test_plan_error_detail_reaches_slack(self):
        """観点: terraform_plan が返した error detail が Slack payload に載る。"""
        result = _run_main_with_plan_result(1, "provider auth failed: 401")
        assert result["called"] is True
        assert "plan 実行エラー" in result["message"]
        assert "r/e" in result["message"]
        assert "provider auth failed: 401" in result["message"]

    def test_drift_summary_reaches_slack(self):
        """観点: drift 検出時は plan JSON を parse したサマリが Slack payload に載る。"""
        plan_json = _plan_json(
            _resource_change("google_storage_bucket.a", "google_storage_bucket", ["create"], None, {}),
        )
        result = _run_main_with_plan_result(2, plan_json)
        assert result["called"] is True
        assert "差分を検出" in result["message"]
        assert "r/e" in result["message"]
        assert "Plan: 1 to add, 0 to change, 0 to destroy." in result["message"]
        assert "google_storage_bucket.a will be created" in result["message"]


class TestMainSuppression:
    """suppress ルール適用後の Slack 通知仕様。

    Terraform 側で activation_policy は ignore_changes から外しているので plan には
    常に差分として載る。dev/stg では targets.yaml の suppress でノイズを消し、
    prod では suppress なしで通知する、という設計を end-to-end で保証する。
    """

    def test_dev_activation_policy_only_is_not_notified(self):
        """観点: dev 相当（suppress 有り）で activation_policy 単独差分なら Slack 通知なし。

        nightly-shutdown / /db-stop が起こす常態的 drift を毎朝通知しないための核心仕様。
        """
        plan_json = _plan_json(_resource_change(
            "module.database.google_sql_database_instance.main",
            CLOUDSQL,
            ["update"],
            {"settings": [{"activation_policy": "ALWAYS"}]},
            {"settings": [{"activation_policy": "NEVER"}]},
        ))
        result = _run_main_with_plan_result(2, plan_json, suppress=ACTIVATION_POLICY_SUPPRESS)
        assert result["called"] is False

    def test_prod_activation_policy_is_notified(self):
        """観点: prod 相当（suppress 無し）で activation_policy 差分があれば Slack 通知される。

        prod の意図しない停止を検知するための要件。検知器側で明示的に
        suppress 対象から外れていることを保証する。
        """
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

    def test_dev_mixed_with_non_suppressed_attribute_is_notified(self):
        """観点: dev でも suppress 対象外の属性（例: tier）が同時に変わっていれば通知する。

        抑止が「リソース内全属性が rule 内」判定のため、1 つでも外れると visible に残す。
        """
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

    def test_dev_other_resource_drift_is_notified_even_with_suppress(self):
        """観点: suppress rule と無関係のリソースに drift があれば通知する。

        dev の suppress が「Cloud SQL 以外も黙らせる」副作用を持たないことを保証する。
        """
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

    def test_no_drift_does_not_notify(self):
        """観点: plan exit 0 (差分なし) なら suppress の有無に関係なく通知しない。"""
        result = _run_main_with_plan_result(0, "", suppress=ACTIVATION_POLICY_SUPPRESS)
        assert result["called"] is False
