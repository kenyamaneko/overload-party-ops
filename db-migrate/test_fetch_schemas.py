#!/usr/bin/env python3
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_MODULE_PATH = Path(__file__).parent / "fetch-schemas.py"
_spec = importlib.util.spec_from_file_location("fetch_schemas", _MODULE_PATH)
fetch_schemas = importlib.util.module_from_spec(_spec)
sys.modules["fetch_schemas"] = fetch_schemas
_spec.loader.exec_module(fetch_schemas)


class Testスキーマ名検証:
    """schemas.lock.yaml から取得したスキーマ名がファイルパスや SQL に到達する前の
    多重防御の核。パストラバーサル・シェルメタ文字を確実に弾くことを保証する。
    """

    @pytest.mark.parametrize(
        "schema_name",
        [
            pytest.param("shop", id="小文字英字のスキーマ名 shop のとき"),
            pytest.param("_x", id="アンダースコア開始の _x のとき"),
            pytest.param("a1", id="英数字混在の a1 のとき"),
        ],
    )
    def test_有効なスキーマ名は例外にならない(self, schema_name):
        fetch_schemas._validate_schema_name(schema_name, source="test")

    @pytest.mark.parametrize(
        "schema_name",
        [
            pytest.param("../x", id="パストラバーサルの ../x のとき"),
            pytest.param("Foo", id="大文字を含む Foo のとき"),
            pytest.param("1a", id="数字開始の 1a のとき"),
            pytest.param("a-b", id="ハイフンを含む a-b のとき"),
            pytest.param("a;b", id="シェルメタ文字を含む a;b のとき"),
            pytest.param("", id="空文字のとき"),
            pytest.param(123, id="文字列でない 123 のとき"),
        ],
    )
    def test_不正なスキーマ名はSystemExitで中断する(self, schema_name):
        with pytest.raises(SystemExit, match="invalid schema name"):
            fetch_schemas._validate_schema_name(schema_name, source="test")


class Test単一ファイルのsparse_clone:
    """subprocess は外部境界としてダブル化する。checkout 成功を模す fake は dest 配下に
    ファイルを書き、以降のファイル存在チェック分岐を実データで通す。
    """

    def _fake_checkout_writes_file(self, file_path: str):
        """checkout コマンド実行時に dest 配下へ期待ファイルを書く _run の fake を返す。

        Args:
            file_path: schemas.lock.yaml エントリの path (dest からの相対パス)。

        Returns:
            _run と同じシグネチャの callable。
        """
        def fake_run(cmd, cwd=None, env=None):
            if cmd[:2] == ["git", "checkout"]:
                target = Path(cwd) / file_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("CREATE TABLE t (id INT);")
        return fake_run

    def _record_git_calls(self, tmp_path, token):
        """_clone_sparse を fake の _run で実行し、渡されたコマンドと環境変数を記録する。

        Args:
            tmp_path: clone 先ディレクトリ。
            token: _clone_sparse に渡す GitHub PAT。

        Returns:
            (コマンド列のリスト, 環境変数のリスト) の組。
        """
        commands = []
        envs = []
        writes_file = self._fake_checkout_writes_file("db/schema.sql")

        def fake_run(cmd, cwd=None, env=None):
            commands.append(cmd)
            envs.append(env)
            writes_file(cmd, cwd=cwd)

        with patch("fetch_schemas._run", side_effect=fake_run):
            fetch_schemas._clone_sparse("o/r", "main", "db/schema.sql", tmp_path, token)
        return commands, envs

    def test_tokenがあるとき取得元URLにtokenを含めない(self, tmp_path):
        commands, _ = self._record_git_calls(tmp_path, "TSTTOKEN")

        remote_calls = [c for c in commands if c[:2] == ["git", "remote"]]
        assert remote_calls[0][-1] == "https://github.com/o/r.git"
        assert not any("TSTTOKEN" in arg for cmd in commands for arg in cmd)

    def test_tokenがあるときgitの設定として環境変数でtokenを渡す(self, tmp_path):
        _, envs = self._record_git_calls(tmp_path, "TSTTOKEN")

        assert envs[0]["GIT_CONFIG_COUNT"] == "1"
        assert envs[0]["GIT_CONFIG_KEY_0"] == (
            "url.https://x-access-token:TSTTOKEN@github.com/.insteadOf"
        )
        assert envs[0]["GIT_CONFIG_VALUE_0"] == "https://github.com/"

    def test_tokenが無いとき認証の設定を付けない(self, tmp_path):
        commands, envs = self._record_git_calls(tmp_path, None)

        remote_calls = [c for c in commands if c[:2] == ["git", "remote"]]
        assert remote_calls[0][-1] == "https://github.com/o/r.git"
        assert "GIT_CONFIG_COUNT" not in envs[0]

    def test_git操作が失敗したときlockエントリ情報付きのSystemExitで中断する(self, tmp_path):
        with patch("fetch_schemas._run", side_effect=subprocess.CalledProcessError(1, ["git"])):
            with pytest.raises(SystemExit) as exc:
                fetch_schemas._clone_sparse("o/r", "main", "db/schema.sql", tmp_path, None)
        assert "git operation failed" in str(exc.value)
        assert "o/r@main" in str(exc.value)

    def test_clone後に期待ファイルが無いときstale_lockとしてSystemExitで中断する(self, tmp_path):
        with patch("fetch_schemas._run"):
            with pytest.raises(SystemExit, match="stale"):
                fetch_schemas._clone_sparse("o/r", "main", "db/schema.sql", tmp_path, None)

    def test_cloneが成功したとき取得したファイルのパスを返す(self, tmp_path):
        with patch("fetch_schemas._run", side_effect=self._fake_checkout_writes_file("db/schema.sql")):
            result = fetch_schemas._clone_sparse("o/r", "main", "db/schema.sql", tmp_path, None)
        assert result == tmp_path / "db" / "schema.sql"


class TestユニオンSQLの結合:
    """union は下流の psqldef が適用するため、実 DB 適用ではなく構成 (バナーと DDL の
    包含・順序) を検証する。
    """

    def _fake_clone_sparse(self, ddl_by_name: dict[str, str]):
        """スキーマ名ごとの DDL 文字列を返す _clone_sparse の fake を作る。

        Args:
            ddl_by_name: スキーマ名 -> DDL 文字列の辞書。

        Returns:
            _clone_sparse と同じシグネチャの callable。呼び出し引数を calls 属性に記録する。
        """
        calls = []

        def fake(repo, ref, file_path, dest, token):
            calls.append({"repo": repo, "ref": ref, "file_path": file_path, "token": token})
            target = dest / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(ddl_by_name[dest.name])
            return target

        fake.calls = calls
        return fake

    def test_schemasキーが無いときSystemExitで中断する(self, tmp_path):
        with pytest.raises(SystemExit, match=r"must contain a `schemas:` list"):
            fetch_schemas.build_union({"x": []}, tmp_path, None, None)

    def test_schemasがリストでないときSystemExitで中断する(self, tmp_path):
        with pytest.raises(SystemExit, match=r"must contain a `schemas:` list"):
            fetch_schemas.build_union({"schemas": {}}, tmp_path, None, None)

    def test_エントリが1件のときバナーとそのDDLがunionに載る(self, tmp_path):
        lock = {"schemas": [{"name": "shop", "repo": "o/r", "path": "db.sql"}]}
        fake = self._fake_clone_sparse({"shop": "CREATE TABLE t1 (id INT);"})
        with patch("fetch_schemas._clone_sparse", side_effect=fake):
            union = fetch_schemas.build_union(lock, tmp_path, None, None)
        assert "-- [shop]  source: o/r@main  path: db.sql" in union
        assert "CREATE TABLE t1 (id INT);" in union

    def test_エントリが複数件のときlock記載順にDDLが結合される(self, tmp_path):
        lock = {"schemas": [
            {"name": "shop", "repo": "o/shop", "path": "db.sql"},
            {"name": "card", "repo": "o/card", "path": "db.sql"},
        ]}
        fake = self._fake_clone_sparse({
            "shop": "CREATE TABLE shop_t (id INT);",
            "card": "CREATE TABLE card_t (id INT);",
        })
        with patch("fetch_schemas._clone_sparse", side_effect=fake):
            union = fetch_schemas.build_union(lock, tmp_path, None, None)
        assert union.index("[shop]") < union.index("[card]")
        assert "CREATE TABLE shop_t (id INT);" in union
        assert "CREATE TABLE card_t (id INT);" in union

    def test_エントリにrefが無いときmainが使われる(self, tmp_path):
        lock = {"schemas": [{"name": "shop", "repo": "o/r", "path": "db.sql"}]}
        fake = self._fake_clone_sparse({"shop": "CREATE TABLE t1 (id INT);"})
        with patch("fetch_schemas._clone_sparse", side_effect=fake):
            union = fetch_schemas.build_union(lock, tmp_path, None, None)
        assert "@main" in union
        assert fake.calls[0]["ref"] == "main"

    def test_ref_overrideを指定したとき全エントリのrefが上書きされる(self, tmp_path):
        lock = {"schemas": [{"name": "shop", "repo": "o/r", "path": "db.sql", "ref": "v1.0.0"}]}
        fake = self._fake_clone_sparse({"shop": "CREATE TABLE t1 (id INT);"})
        with patch("fetch_schemas._clone_sparse", side_effect=fake):
            union = fetch_schemas.build_union(lock, tmp_path, None, "TST-REF")
        assert "@TST-REF" in union
        assert "@v1.0.0" not in union

    def test_スキーマ名が不正なときcloneせずSystemExitで中断する(self, tmp_path):
        lock = {"schemas": [{"name": "../x", "repo": "o/r", "path": "db.sql"}]}
        with patch("fetch_schemas._clone_sparse") as clone:
            with pytest.raises(SystemExit):
                fetch_schemas.build_union(lock, tmp_path, None, None)
        clone.assert_not_called()


class Testスキーマ取得mainの入口:
    def _write_lock(self, tmp_path: Path) -> Path:
        """schemas 0 件の schemas.lock.yaml を作成する。

        Args:
            tmp_path: pytest が用意する一時ディレクトリ。

        Returns:
            作成した lock ファイルのパス。
        """
        lock_path = tmp_path / "schemas.lock.yaml"
        lock_path.write_text("schemas: []\n")
        return lock_path

    def _base_argv(self, tmp_path: Path, lock_path: Path, out_path: Path, grant_src: Path) -> list[str]:
        """main() の必須引数 (--workdir 含む) を組み立てる。

        Args:
            tmp_path: pytest が用意する一時ディレクトリ。
            lock_path: --lock に渡す schemas.lock.yaml のパス。
            out_path: --out に渡す union 出力先パス。
            grant_src: --grant-src に渡す grant_iam.sql のパス。

        Returns:
            sys.argv に設定する引数リスト。
        """
        return [
            "fetch-schemas",
            "--lock", str(lock_path),
            "--out", str(out_path),
            "--grant-src", str(grant_src),
            "--workdir", str(tmp_path / "workdir"),
        ]

    @pytest.mark.parametrize(
        ("cli_token", "env_vars", "expected_token"),
        [
            pytest.param(
                "TST1", {"GITHUB_TOKEN": "TST2"}, "TST1",
                id="--token 指定があるとき",
            ),
            pytest.param(
                None, {"GITHUB_TOKEN": "TST2", "DB_MIGRATE_TOKEN": "TST3"}, "TST2",
                id="--token が無く GITHUB_TOKEN があるとき",
            ),
            pytest.param(
                None, {"DB_MIGRATE_TOKEN": "TST3"}, "TST3",
                id="GITHUB_TOKEN も無いとき",
            ),
        ],
    )
    def test_token解決の優先順位(self, tmp_path, monkeypatch, cli_token, env_vars, expected_token):
        lock_path = self._write_lock(tmp_path)
        grant_src = tmp_path / "grant_iam.sql"
        grant_src.write_text("GRANT SELECT ON t TO r;")
        out_path = tmp_path / "sql" / "schema_union.sql"

        argv = self._base_argv(tmp_path, lock_path, out_path, grant_src)
        if cli_token is not None:
            argv += ["--token", cli_token]
        monkeypatch.setattr("sys.argv", argv)

        with patch.dict(os.environ, env_vars, clear=True), \
             patch("fetch_schemas.build_union", return_value="union sql") as build_union:
            fetch_schemas.main()
        assert build_union.call_args.args[2] == expected_token

    def test_grant_iam_sqlが無いときSystemExitで中断する(self, tmp_path, monkeypatch):
        lock_path = self._write_lock(tmp_path)
        out_path = tmp_path / "sql" / "schema_union.sql"
        missing_grant_src = tmp_path / "no_such_grant.sql"

        argv = self._base_argv(tmp_path, lock_path, out_path, missing_grant_src)
        monkeypatch.setattr("sys.argv", argv)

        with patch.dict(os.environ, {}, clear=True), \
             patch("fetch_schemas.build_union", return_value="union sql"):
            with pytest.raises(SystemExit, match="grant_iam.sql not found"):
                fetch_schemas.main()

    def test_正常のときunionがoutに書かれgrant_iam_sqlが同じディレクトリに複製される(self, tmp_path, monkeypatch):
        lock_path = self._write_lock(tmp_path)
        grant_src = tmp_path / "grant_iam.sql"
        grant_src.write_text("GRANT SELECT ON t TO r;")
        out_path = tmp_path / "sql" / "schema_union.sql"

        argv = self._base_argv(tmp_path, lock_path, out_path, grant_src)
        monkeypatch.setattr("sys.argv", argv)

        with patch.dict(os.environ, {}, clear=True), \
             patch("fetch_schemas.build_union", return_value="union sql"):
            fetch_schemas.main()

        assert out_path.read_text() == "union sql"
        assert (out_path.parent / "grant_iam.sql").read_text() == grant_src.read_text()
