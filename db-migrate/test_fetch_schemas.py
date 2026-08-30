#!/usr/bin/env python3
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from schema_check import parse_schema

_MODULE_PATH = Path(__file__).parent / "fetch-schemas.py"
_spec = importlib.util.spec_from_file_location("fetch_schemas", _MODULE_PATH)
fetch_schemas = importlib.util.module_from_spec(_spec)
sys.modules["fetch_schemas"] = fetch_schemas
_spec.loader.exec_module(fetch_schemas)


class Testスキーマ名検証:
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
            fetch_schemas._clone_sparse("o/r", "main", ["db/schema.sql"], tmp_path, token)
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

    def test_呼び出し元の環境が既にgit設定を持つときSystemExitで中断する(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
        with pytest.raises(SystemExit, match="GIT_CONFIG_COUNT is already set"):
            self._record_git_calls(tmp_path, "TSTTOKEN")

    def test_tokenが無いとき認証の設定を付けない(self, tmp_path):
        commands, envs = self._record_git_calls(tmp_path, None)

        remote_calls = [c for c in commands if c[:2] == ["git", "remote"]]
        assert remote_calls[0][-1] == "https://github.com/o/r.git"
        assert "GIT_CONFIG_COUNT" not in envs[0]

    def test_git操作が失敗したときlockエントリ情報付きのSystemExitで中断する(self, tmp_path):
        with patch("fetch_schemas._run", side_effect=subprocess.CalledProcessError(1, ["git"])):
            with pytest.raises(SystemExit) as exc:
                fetch_schemas._clone_sparse("o/r", "main", ["db/schema.sql"], tmp_path, None)
        assert "git operation failed" in str(exc.value)
        assert "o/r@main" in str(exc.value)

    def test_clone後に期待ファイルが無いときstale_lockとしてSystemExitで中断する(self, tmp_path):
        with patch("fetch_schemas._run"):
            with pytest.raises(SystemExit, match="stale"):
                fetch_schemas._clone_sparse("o/r", "main", ["db/schema.sql"], tmp_path, None)

    def test_cloneが成功したとき取得したファイルのパスを返す(self, tmp_path):
        with patch("fetch_schemas._run", side_effect=self._fake_checkout_writes_file("db/schema.sql")):
            result = fetch_schemas._clone_sparse("o/r", "main", ["db/schema.sql"], tmp_path, None)
        assert result == [tmp_path / "db" / "schema.sql"]

    def test_複数ファイルを求めたとき要求した順にパスを返す(self, tmp_path):
        def fake_run(cmd, cwd=None, env=None):
            if cmd[:2] == ["git", "checkout"]:
                for file_path in ("db/schema.sql", "db/seed/a.sql"):
                    target = Path(cwd) / file_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("SQL")

        with patch("fetch_schemas._run", side_effect=fake_run):
            result = fetch_schemas._clone_sparse(
                "o/r", "main", ["db/schema.sql", "db/seed/a.sql"], tmp_path, None)
        assert result == [tmp_path / "db" / "schema.sql", tmp_path / "db" / "seed" / "a.sql"]


class TestユニオンSQLの結合:
    def _fake_clone_sparse(self, sql_by_name: dict[str, str]):
        """スキーマ名ごとの SQL 文字列を返す _clone_sparse の fake を作る。

        Args:
            sql_by_name: スキーマ名 -> 取得したファイルに書き込む SQL 文字列の辞書。

        Returns:
            _clone_sparse と同じシグネチャの callable。呼び出し引数を calls 属性に記録する。
        """
        calls = []

        def fake(repo, ref, file_paths, dest, token):
            calls.append({"repo": repo, "ref": ref, "file_paths": file_paths, "token": token})
            targets = []
            for file_path in file_paths:
                target = dest / file_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(sql_by_name[dest.name])
                targets.append(target)
            return targets

        fake.calls = calls
        return fake

    def test_schemasキーが無いときSystemExitで中断する(self, tmp_path):
        with pytest.raises(SystemExit, match=r"must contain a `schemas:` list"):
            fetch_schemas.fetch_sources({"x": []}, tmp_path, None, None, with_seeds=False)

    def test_schemasがリストでないときSystemExitで中断する(self, tmp_path):
        with pytest.raises(SystemExit, match=r"must contain a `schemas:` list"):
            fetch_schemas.fetch_sources({"schemas": {}}, tmp_path, None, None, with_seeds=False)

    def test_エントリが1件のときバナーとそのDDLがunionに載る(self, tmp_path):
        lock = {"schemas": [{"name": "shop", "repo": "o/r", "path": "db.sql", "ref": "main"}]}
        fake = self._fake_clone_sparse({"shop": "CREATE TABLE t1 (id INT);"})
        with patch("fetch_schemas._clone_sparse", side_effect=fake):
            sources = fetch_schemas.fetch_sources(lock, tmp_path, None, None, with_seeds=False)
        union = fetch_schemas.render_union(sources)
        assert "-- [shop]  source: o/r@main  path: db.sql" in union
        assert "CREATE TABLE t1 (id INT);" in union

    def test_エントリが複数件のときlock記載順にDDLが結合される(self, tmp_path):
        lock = {"schemas": [
            {"name": "shop", "repo": "o/shop", "path": "db.sql", "ref": "main"},
            {"name": "card", "repo": "o/card", "path": "db.sql", "ref": "main"},
        ]}
        fake = self._fake_clone_sparse({
            "shop": "CREATE TABLE shop_t (id INT);",
            "card": "CREATE TABLE card_t (id INT);",
        })
        with patch("fetch_schemas._clone_sparse", side_effect=fake):
            sources = fetch_schemas.fetch_sources(lock, tmp_path, None, None, with_seeds=False)
        union = fetch_schemas.render_union(sources)
        assert union.index("[shop]") < union.index("[card]")
        assert "CREATE TABLE shop_t (id INT);" in union
        assert "CREATE TABLE card_t (id INT);" in union

    def test_同名テーブルを持つ2サービスを結合したunionは破壊的変更チェックに別テーブルとして読まれる(self, tmp_path):
        lock = {"schemas": [
            {"name": "shop", "repo": "o/shop", "path": "db.sql", "ref": "main"},
            {"name": "scenario", "repo": "o/scenario", "path": "db.sql", "ref": "main"},
        ]}
        fake = self._fake_clone_sparse({
            "shop": "CREATE TABLE shop.outbox_events (id UUID PRIMARY KEY, payload JSONB);",
            "scenario": "CREATE TABLE scenario.outbox_events (id UUID PRIMARY KEY);",
        })
        with patch("fetch_schemas._clone_sparse", side_effect=fake):
            sources = fetch_schemas.fetch_sources(lock, tmp_path, None, None, with_seeds=False)
        assert parse_schema(fetch_schemas.render_union(sources)) == {
            "shop.outbox_events": {"id", "payload"},
            "scenario.outbox_events": {"id"},
        }

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param(
                {"name": "shop", "repo": "o/r", "path": "db.sql"},
                id="ref キーが無いとき、取得せず中断する",
            ),
            pytest.param(
                {"name": "shop", "repo": "o/r", "path": "db.sql", "ref": None},
                id="ref が空のとき、取得せず中断する",
            ),
        ],
    )
    def test_refがpinされていないエントリはSystemExitで中断する(self, tmp_path, entry):
        with patch("fetch_schemas._clone_sparse") as clone:
            with pytest.raises(SystemExit, match=r"has no `ref`"):
                fetch_schemas.fetch_sources({"schemas": [entry]}, tmp_path, None, None,
                                            with_seeds=False)
        clone.assert_not_called()

    def test_ref_overrideを指定してもエントリのref欠落はSystemExitで中断する(self, tmp_path):
        lock = {"schemas": [{"name": "shop", "repo": "o/r", "path": "db.sql"}]}
        with patch("fetch_schemas._clone_sparse") as clone:
            with pytest.raises(SystemExit, match=r"has no `ref`"):
                fetch_schemas.fetch_sources(lock, tmp_path, None, "TST-REF", with_seeds=False)
        clone.assert_not_called()

    def test_ref_overrideを指定したとき全エントリのrefが上書きされる(self, tmp_path):
        lock = {"schemas": [{"name": "shop", "repo": "o/r", "path": "db.sql", "ref": "v1.0.0"}]}
        fake = self._fake_clone_sparse({"shop": "CREATE TABLE t1 (id INT);"})
        with patch("fetch_schemas._clone_sparse", side_effect=fake):
            sources = fetch_schemas.fetch_sources(lock, tmp_path, None, "TST-REF", with_seeds=False)
        union = fetch_schemas.render_union(sources)
        assert "@TST-REF" in union
        assert "@v1.0.0" not in union

    def test_スキーマ名が不正なときcloneせずSystemExitで中断する(self, tmp_path):
        lock = {"schemas": [{"name": "../x", "repo": "o/r", "path": "db.sql", "ref": "main"}]}
        with patch("fetch_schemas._clone_sparse") as clone:
            with pytest.raises(SystemExit):
                fetch_schemas.fetch_sources(lock, tmp_path, None, None, with_seeds=False)
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

    def test_token引数とGITHUB_TOKEN環境変数の両方があるときtoken引数の値がスキーマ取得に使うトークンになる(self, tmp_path, monkeypatch):
        lock_path = self._write_lock(tmp_path)
        grant_src = tmp_path / "grant_iam.sql"
        grant_src.write_text("GRANT SELECT ON t TO r;")
        out_path = tmp_path / "sql" / "schema_union.sql"

        argv = self._base_argv(tmp_path, lock_path, out_path, grant_src)
        argv += ["--token", "TST1"]
        monkeypatch.setattr("sys.argv", argv)

        with patch.dict(os.environ, {"GITHUB_TOKEN": "TST2"}, clear=True), \
             patch("fetch_schemas.fetch_sources", return_value=[]) as fetch_sources:
            fetch_schemas.main()
        assert fetch_sources.call_args.args[2] == "TST1"

    @pytest.mark.parametrize(
        ("env_vars", "expected_token"),
        [
            pytest.param(
                {"GITHUB_TOKEN": "TST2", "DB_MIGRATE_TOKEN": "TST3"}, "TST2",
                id="GITHUB_TOKEN と DB_MIGRATE_TOKEN の両方があるとき、GITHUB_TOKEN がスキーマ取得に使うトークンになる",
            ),
            pytest.param(
                {"DB_MIGRATE_TOKEN": "TST3"}, "TST3",
                id="GITHUB_TOKEN が無いとき、DB_MIGRATE_TOKEN がスキーマ取得に使うトークンになる",
            ),
        ],
    )
    def test_token解決(self, tmp_path, monkeypatch, env_vars, expected_token):
        lock_path = self._write_lock(tmp_path)
        grant_src = tmp_path / "grant_iam.sql"
        grant_src.write_text("GRANT SELECT ON t TO r;")
        out_path = tmp_path / "sql" / "schema_union.sql"

        argv = self._base_argv(tmp_path, lock_path, out_path, grant_src)
        monkeypatch.setattr("sys.argv", argv)

        with patch.dict(os.environ, env_vars, clear=True), \
             patch("fetch_schemas.fetch_sources", return_value=[]) as fetch_sources:
            fetch_schemas.main()
        assert fetch_sources.call_args.args[2] == expected_token

    def test_grant_iam_sqlが無いときSystemExitで中断する(self, tmp_path, monkeypatch):
        lock_path = self._write_lock(tmp_path)
        out_path = tmp_path / "sql" / "schema_union.sql"
        missing_grant_src = tmp_path / "no_such_grant.sql"

        argv = self._base_argv(tmp_path, lock_path, out_path, missing_grant_src)
        monkeypatch.setattr("sys.argv", argv)

        with patch.dict(os.environ, {}, clear=True), \
             patch("fetch_schemas.render_union", return_value="union sql"):
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
             patch("fetch_schemas.render_union", return_value="union sql"):
            fetch_schemas.main()

        assert out_path.read_text() == "union sql"
        assert (out_path.parent / "grant_iam.sql").read_text() == grant_src.read_text()

    def test_seedの出力先を指定しないときseed_unionは書かれない(self, tmp_path, monkeypatch):
        lock_path = self._write_lock(tmp_path)
        grant_src = tmp_path / "grant_iam.sql"
        grant_src.write_text("GRANT SELECT ON t TO r;")
        out_path = tmp_path / "sql" / "schema_union.sql"

        monkeypatch.setattr("sys.argv", self._base_argv(tmp_path, lock_path, out_path, grant_src))

        with patch.dict(os.environ, {}, clear=True):
            fetch_schemas.main()

        assert not (out_path.parent / "seed_union.sql").exists()

    def test_seedの出力先を指定したときseed_unionも書かれる(self, tmp_path, monkeypatch):
        lock_path = self._write_lock(tmp_path)
        grant_src = tmp_path / "grant_iam.sql"
        grant_src.write_text("GRANT SELECT ON t TO r;")
        out_path = tmp_path / "sql" / "schema_union.sql"
        seed_out_path = tmp_path / "sql" / "seed_union.sql"

        argv = self._base_argv(tmp_path, lock_path, out_path, grant_src)
        argv += ["--seed-out", str(seed_out_path)]
        monkeypatch.setattr("sys.argv", argv)

        with patch.dict(os.environ, {}, clear=True):
            fetch_schemas.main()

        assert "seed_union.sql" in seed_out_path.read_text()


class Testマスタデータ投入SQLの結合:
    def _source(self, name: str, schema: str = "CREATE TABLE t ();", seeds=()) -> dict:
        """fetch_sources が返す形のエントリを組み立てます。

        Args:
            name: スキーマ名。
            schema: そのサービスの DDL。
            seeds: (パス, SQL) の組のリスト。

        Returns:
            render_union / render_seed_union に渡せる辞書。
        """
        return {
            "name": name,
            "repo": f"kenyamaneko/overload-party-{name}",
            "ref": "main",
            "path": "db/schema.sql",
            "schema": schema,
            "seeds": list(seeds),
        }

    def test_複数サービスのseedがlock記載順に結合される(self):
        sources = [
            self._source("card", seeds=[("db/seed/a.sql", "INSERT INTO card.a;"),
                                        ("db/seed/b.sql", "INSERT INTO card.b;")]),
            self._source("shop", seeds=[("db/seed/c.sql", "INSERT INTO shop.c;")]),
        ]

        union = fetch_schemas.render_seed_union(sources)

        assert union.index("INSERT INTO card.a;") < union.index("INSERT INTO card.b;")
        assert union.index("INSERT INTO card.b;") < union.index("INSERT INTO shop.c;")

    def test_seedを持たないサービスは結合結果に現れない(self):
        sources = [
            self._source("account"),
            self._source("card", seeds=[("db/seed/a.sql", "INSERT INTO card.a;")]),
        ]

        union = fetch_schemas.render_seed_union(sources)

        assert "INSERT INTO card.a;" in union
        assert "account" not in union

    def test_seedが1件も無いとき生成物の見出しだけになる(self):
        union = fetch_schemas.render_seed_union([self._source("account")])

        assert "seed_union.sql" in union
        assert "INSERT" not in union

    def test_取得元のリポジトリと参照とパスが出力に残る(self):
        sources = [self._source("card", seeds=[("db/seed/a.sql", "INSERT INTO card.a;")])]

        union = fetch_schemas.render_seed_union(sources)

        assert "kenyamaneko/overload-party-card@main" in union
        assert "db/seed/a.sql" in union

    def test_DDLの結合にseedのSQLは混ざらない(self):
        sources = [self._source("card", schema="CREATE TABLE card.card_definitions ();",
                                seeds=[("db/seed/a.sql", "INSERT INTO card.a;")])]

        union = fetch_schemas.render_union(sources)

        assert "CREATE TABLE card.card_definitions ();" in union
        assert "INSERT INTO card.a;" not in union


class Test取得対象の決定:
    def _fake_clone_sparse(self, requested: list):
        """要求されたファイルを記録し、その場に SQL を書く _clone_sparse の fake を作る。

        Args:
            requested: 要求されたファイルパスを追記するリスト。

        Returns:
            _clone_sparse と同じシグネチャの callable。
        """
        def fake(repo, ref, file_paths, dest, token):
            requested.extend(file_paths)
            targets = []
            for file_path in file_paths:
                target = dest / file_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"SQL {file_path}")
                targets.append(target)
            return targets

        return fake

    def test_seedを求めないとき取得するのはDDLだけになる(self, tmp_path):
        requested = []
        lock = {"schemas": [{"name": "card", "repo": "o/card", "path": "db/schema.sql",
                             "ref": "main", "seeds": ["db/seed/a.sql"]}]}

        with patch("fetch_schemas._clone_sparse", side_effect=self._fake_clone_sparse(requested)):
            sources = fetch_schemas.fetch_sources(lock, tmp_path, None, None, with_seeds=False)

        assert requested == ["db/schema.sql"]
        assert sources[0]["seeds"] == []

    def test_seedを求めるときDDLと同じ取得でseedも取る(self, tmp_path):
        requested = []
        lock = {"schemas": [{"name": "card", "repo": "o/card", "path": "db/schema.sql",
                             "ref": "main", "seeds": ["db/seed/a.sql"]}]}

        with patch("fetch_schemas._clone_sparse", side_effect=self._fake_clone_sparse(requested)):
            sources = fetch_schemas.fetch_sources(lock, tmp_path, None, None, with_seeds=True)

        assert requested == ["db/schema.sql", "db/seed/a.sql"]
        assert sources[0]["seeds"] == [("db/seed/a.sql", "SQL db/seed/a.sql")]
