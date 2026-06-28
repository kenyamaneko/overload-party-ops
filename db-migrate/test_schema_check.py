#!/usr/bin/env python3
import tempfile
import os
import pytest
from schema_check import parse_schema, check, main, _extract_columns


def _write_schema(tmp_path, name: str, content: str) -> str:
    """スキーマ SQL を一時ファイルに書き出してパスを返します。

    Args:
        tmp_path: pytest が用意する一時ディレクトリ。
        name: 作成するファイル名。
        content: 書き込む SQL 文字列。

    Returns:
        作成した一時ファイルの絶対パス文字列。
    """
    path = tmp_path / name
    path.write_text(content)
    return str(path)


class TestParseSchema:
    def test_basic_table(self):
        sql = """
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email TEXT
        );
        """
        tables = parse_schema(sql)
        assert "users" in tables
        assert tables["users"] == {"id", "name", "email"}

    def test_multiple_tables(self):
        sql = """
        CREATE TABLE games (
            id SERIAL PRIMARY KEY,
            status VARCHAR(20)
        );
        CREATE TABLE players (
            id SERIAL PRIMARY KEY,
            game_id INTEGER
        );
        """
        tables = parse_schema(sql)
        assert len(tables) == 2
        assert "games" in tables
        assert "players" in tables

    def test_constraint_keywords_excluded(self):
        sql = """
        CREATE TABLE orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE (user_id)
        );
        """
        tables = parse_schema(sql)
        assert "id" in tables["orders"]
        assert "user_id" in tables["orders"]
        assert len(tables["orders"]) == 2

    def test_check_constraint_with_commas(self):
        sql = """
        CREATE TABLE player_factions (
            id SERIAL PRIMARY KEY,
            faction VARCHAR(20) NOT NULL CHECK (faction IN ('SHE', 'Tenki', 'Sugar', 'Tuners'))
        );
        """
        tables = parse_schema(sql)
        assert "id" in tables["player_factions"]
        assert "faction" in tables["player_factions"]

    def test_empty_schema(self):
        tables = parse_schema("")
        assert tables == {}

    def test_case_insensitive(self):
        sql = """
        create table Users (
            ID serial primary key,
            Name varchar(255)
        );
        """
        tables = parse_schema(sql)
        assert "users" in tables
        assert "id" in tables["users"]
        assert "name" in tables["users"]


class TestCheck:
    def _write_temp(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_no_changes(self):
        sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = self._write_temp(sql)
        new = self._write_temp(sql)
        try:
            warnings = check(old, new)
            assert warnings == []
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_add_column(self):
        old_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY);"
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert warnings == []
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_drop_column(self):
        old_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT, email TEXT);"
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert len(warnings) == 1
            assert "DROP COLUMN: users.email" in warnings[0]
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_drop_table(self):
        old_sql = """
        CREATE TABLE users (id SERIAL PRIMARY KEY);
        CREATE TABLE logs (id SERIAL PRIMARY KEY);
        """
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY);"
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert len(warnings) == 1
            assert "DROP TABLE: logs" in warnings[0]
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_empty_old_schema(self):
        old_sql = ""
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert warnings == []
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_add_table(self):
        old_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY);"
        new_sql = """
        CREATE TABLE users (id SERIAL PRIMARY KEY);
        CREATE TABLE logs (id SERIAL PRIMARY KEY, message TEXT);
        """
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert warnings == []
        finally:
            os.unlink(old)
            os.unlink(new)


class TestParseSchemaQualifiedTableNames:
    """schema 修飾付きテーブル名を unqualified 名に正規化する仕様。

    同一テーブルが片側だけ `schema.` 付きで書かれても別テーブルと誤認しないよう、
    キーは常に修飾子を外した名前にする。
    """

    @pytest.mark.parametrize("qualifier", ["", "app.", "public.", "MySchema."])
    def test_qualifier_is_stripped_from_table_key(self, qualifier):
        """観点: schema 修飾子の有無・内容にかかわらずテーブルキーは unqualified 名になる。

        Args:
            qualifier: CREATE TABLE に付与する schema 修飾子（空文字は無修飾）。
        """
        sql = f"CREATE TABLE {qualifier}users (id SERIAL PRIMARY KEY, name TEXT);"
        tables = parse_schema(sql)
        assert set(tables) == {"users"}
        assert tables["users"] == {"id", "name"}


class TestExtractColumnsIdentifierFolding:
    """PostgreSQL の識別子畳み込みに合わせてカラム名を正規化する仕様。"""

    @pytest.mark.parametrize(
        "definition,expected",
        [
            ('"order" INTEGER', "order"),
            ('"Order" INTEGER', "Order"),
            ('"group" TEXT NOT NULL', "group"),
            ("Users INTEGER", "users"),
            ("created_at TIMESTAMP", "created_at"),
        ],
    )
    def test_quoted_preserves_case_unquoted_is_lowercased(self, definition, expected):
        """観点: 引用符付き識別子は大小を保持し、引用符なしは小文字へ畳む (PostgreSQL の識別子規則)。

        Args:
            definition: 1 カラム分の DDL 断片。
            expected: 抽出されるべき正規化後カラム名。
        """
        columns = _extract_columns(definition)
        assert columns == {expected}


class TestCheckMultipleDestructiveChanges:
    """複数の破壊的変更をまとめて検出し、警告を整列して返す仕様。"""

    def test_multiple_table_and_column_drops_are_all_reported_sorted(self, tmp_path):
        """観点: 複数テーブル削除と複数カラム削除が全て検出され、整列順で並ぶ。

        Args:
            tmp_path: pytest が用意する一時ディレクトリ。
        """
        old = _write_schema(
            tmp_path,
            "old.sql",
            """
            CREATE TABLE one (id SERIAL PRIMARY KEY, a TEXT, b TEXT, c TEXT);
            CREATE TABLE two (id SERIAL PRIMARY KEY);
            CREATE TABLE three (id SERIAL PRIMARY KEY);
            """,
        )
        new = _write_schema(tmp_path, "new.sql", "CREATE TABLE one (id SERIAL PRIMARY KEY);")
        warnings = check(old, new)
        assert warnings == [
            "DROP TABLE: three",
            "DROP TABLE: two",
            "DROP COLUMN: one.a",
            "DROP COLUMN: one.b",
            "DROP COLUMN: one.c",
        ]

    @pytest.mark.parametrize(
        "old_qualifier,new_qualifier",
        [
            ("app.", ""),
            ("", "app."),
            ("app.", "public."),
        ],
    )
    def test_schema_qualifier_change_is_not_reported_as_drop(self, tmp_path, old_qualifier, new_qualifier):
        """観点: 同一テーブルの schema 修飾子が変わっただけでは破壊的変更扱いしない。

        Args:
            tmp_path: pytest が用意する一時ディレクトリ。
            old_qualifier: 旧スキーマでテーブルに付ける修飾子。
            new_qualifier: 新スキーマでテーブルに付ける修飾子。
        """
        old = _write_schema(
            tmp_path,
            "old.sql",
            f"CREATE TABLE {old_qualifier}users (id SERIAL PRIMARY KEY, name TEXT);",
        )
        new = _write_schema(
            tmp_path,
            "new.sql",
            f"CREATE TABLE {new_qualifier}users (id SERIAL PRIMARY KEY, name TEXT);",
        )
        warnings = check(old, new)
        assert warnings == []

    def test_dropped_qualified_table_reported_with_unqualified_name(self, tmp_path):
        """観点: schema 修飾付きで定義されたテーブルの削除は unqualified 名で警告される。

        Args:
            tmp_path: pytest が用意する一時ディレクトリ。
        """
        old = _write_schema(
            tmp_path,
            "old.sql",
            """
            CREATE TABLE app.users (id SERIAL PRIMARY KEY);
            CREATE TABLE app.logs (id SERIAL PRIMARY KEY);
            """,
        )
        new = _write_schema(tmp_path, "new.sql", "CREATE TABLE app.users (id SERIAL PRIMARY KEY);")
        warnings = check(old, new)
        assert warnings == ["DROP TABLE: logs"]


class TestMain:
    """CLI が破壊的変更の有無を終了コードで表現する仕様。

    exit 2 = 引数不正、exit 1 = 破壊的変更あり、exit 0 = 安全。
    CI のゲートがこの終了コードで apply 可否を判断する。
    """

    @pytest.mark.parametrize(
        "argv",
        [
            ["schema_check"],
            ["schema_check", "only_one_path"],
            ["schema_check", "a", "b", "c"],
        ],
    )
    def test_wrong_argument_count_exits_2(self, monkeypatch, argv):
        """観点: 引数が old/new の 2 個でなければ exit 2 で使い方エラーを示す。

        Args:
            monkeypatch: sys.argv を差し替えるための pytest フィクスチャ。
            argv: main に渡すコマンドライン引数列（要素数が 3 でないもの）。
        """
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2

    def test_no_destructive_change_exits_0(self, monkeypatch, tmp_path):
        """観点: 破壊的変更が無ければ exit 0 で apply を許可する。

        Args:
            monkeypatch: sys.argv を差し替えるための pytest フィクスチャ。
            tmp_path: pytest が用意する一時ディレクトリ。
        """
        sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = _write_schema(tmp_path, "old.sql", sql)
        new = _write_schema(tmp_path, "new.sql", sql)
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_destructive_change_exits_1_and_lists_warning(self, monkeypatch, tmp_path, capsys):
        """観点: 破壊的変更があれば exit 1 で apply を止め、該当警告を出力する。

        Args:
            monkeypatch: sys.argv を差し替えるための pytest フィクスチャ。
            tmp_path: pytest が用意する一時ディレクトリ。
            capsys: 標準出力を捕捉する pytest フィクスチャ。
        """
        old = _write_schema(
            tmp_path,
            "old.sql",
            "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT, email TEXT);",
        )
        new = _write_schema(tmp_path, "new.sql", "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);")
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "DROP COLUMN: users.email" in capsys.readouterr().out
