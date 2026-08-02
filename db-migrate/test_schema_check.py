#!/usr/bin/env python3
import tempfile
import os
import pytest
from schema_check import SchemaParseError, parse_schema, check, main


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


class TestスキーマDDLのパース:
    def test_基本的なテーブルのカラムを抽出する(self):
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

    def test_IF_NOT_EXISTS付きで定義されたテーブルのカラムを抽出する(self):
        sql = """
        CREATE TABLE IF NOT EXISTS news.news_articles (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT
        );
        """
        tables = parse_schema(sql)
        assert tables["news_articles"] == {"id", "title", "body"}

    def test_複数テーブルをそれぞれ抽出する(self):
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

    def test_制約キーワードはカラムに含めない(self):
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

    def test_CHECK制約内のカンマでカラムを誤分割しない(self):
        sql = """
        CREATE TABLE player_factions (
            id SERIAL PRIMARY KEY,
            faction VARCHAR(20) NOT NULL CHECK (faction IN ('SHE', 'Tenki', 'Sugar', 'Tuners'))
        );
        """
        tables = parse_schema(sql)
        assert "id" in tables["player_factions"]
        assert "faction" in tables["player_factions"]

    def test_空スキーマは空dictになる(self):
        tables = parse_schema("")
        assert tables == {}

    def test_大文字小文字を問わずパースする(self):
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


class Test破壊的変更の検出:
    def _write_temp(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_変更が無ければ警告は空(self):
        sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = self._write_temp(sql)
        new = self._write_temp(sql)
        try:
            warnings = check(old, new)
            assert warnings == []
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_カラム追加は警告しない(self):
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

    def test_カラム削除はDROP_COLUMN警告になる(self):
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

    def test_テーブル削除はDROP_TABLE警告になる(self):
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

    def test_IF_NOT_EXISTS付きで定義されたテーブルの削除もDROP_TABLE警告になる(self, tmp_path):
        old = _write_schema(
            tmp_path,
            "old.sql",
            """
            CREATE TABLE IF NOT EXISTS news.news_articles (id SERIAL PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS news.news_article_translations (id SERIAL PRIMARY KEY);
            """,
        )
        new = _write_schema(
            tmp_path,
            "new.sql",
            "CREATE TABLE IF NOT EXISTS news.news_articles (id SERIAL PRIMARY KEY);",
        )
        assert check(old, new) == ["DROP TABLE: news_article_translations"]

    def test_IF_NOT_EXISTS付きで定義されたテーブルのカラム削除もDROP_COLUMN警告になる(self, tmp_path):
        old = _write_schema(
            tmp_path,
            "old.sql",
            "CREATE TABLE IF NOT EXISTS support.inquiries (id SERIAL PRIMARY KEY, body TEXT);",
        )
        new = _write_schema(
            tmp_path,
            "new.sql",
            "CREATE TABLE IF NOT EXISTS support.inquiries (id SERIAL PRIMARY KEY);",
        )
        assert check(old, new) == ["DROP COLUMN: inquiries.body"]

    def test_旧スキーマが空なら警告しない(self):
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

    def test_テーブル追加は警告しない(self):
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


class Testschema修飾付きテーブル名の正規化:
    """同一テーブルが片側だけ `schema.` 付きで書かれても別テーブルと誤認しないよう、
    キーは常に修飾子を外した名前にする。
    """

    @pytest.mark.parametrize(
        "qualifier",
        [
            pytest.param("", id="無修飾のとき users キーになる"),
            pytest.param("app.", id="app. 修飾でも users キーになる"),
            pytest.param("public.", id="public. 修飾でも users キーになる"),
            pytest.param("MySchema.", id="MySchema. 修飾でも users キーになる"),
        ],
    )
    def test_schema修飾子を外したテーブル名をキーにする(self, qualifier):
        sql = f"CREATE TABLE {qualifier}users (id SERIAL PRIMARY KEY, name TEXT);"
        tables = parse_schema(sql)
        assert set(tables) == {"users"}
        assert tables["users"] == {"id", "name"}


class Test識別子の畳み込み:
    @pytest.mark.parametrize(
        ("column_definition", "expected"),
        [
            pytest.param('"order" INTEGER', "order", id='引用符付き "order" は小文字を保持する'),
            pytest.param('"Order" INTEGER', "Order", id='引用符付き "Order" は大小を保持する'),
            pytest.param('"group" TEXT NOT NULL', "group", id='引用符付き予約語 "group" もそのまま保持する'),
            pytest.param("Users INTEGER", "users", id="引用符なし Users は users へ小文字化する"),
            pytest.param("created_at TIMESTAMP", "created_at", id="引用符なし created_at はそのまま"),
        ],
    )
    def test_引用符付きは大小を保持し引用符なしは小文字へ畳む(self, column_definition, expected):
        # PostgreSQL の識別子規則: 引用符付きは大小を保持、引用符なしは小文字へ畳む。
        tables = parse_schema(f"CREATE TABLE t ({column_definition});")
        assert tables["t"] == {expected}


class Test複数破壊的変更の検出:
    def test_複数テーブル削除と複数カラム削除が全て整列順で報告される(self, tmp_path):
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
        ("old_qualifier", "new_qualifier"),
        [
            pytest.param("app.", "", id="app. から無修飾でも DROP としない"),
            pytest.param("", "app.", id="無修飾から app. でも DROP としない"),
            pytest.param("app.", "public.", id="app. から public. でも DROP としない"),
        ],
    )
    def test_schema修飾子が変わっただけでは破壊的変更にしない(self, tmp_path, old_qualifier, new_qualifier):
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

    def test_修飾付きテーブルの削除はunqualified名で警告される(self, tmp_path):
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


class Test解析できないDDLの検出:
    @pytest.mark.parametrize(
        "unparseable_ddl",
        [
            pytest.param(
                "CREATE TABLE games_2026 PARTITION OF games "
                "FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');",
                id="パーティションの定義が解析できないとき、中断する",
            ),
            pytest.param(
                "CREATE TABLE active_users AS SELECT id FROM users;",
                id="問い合わせ結果からの定義が解析できないとき、中断する",
            ),
        ],
    )
    def test_解析できないCREATE_TABLEが混ざるとき件数の食い違いを理由に中断する(self, unparseable_ddl):
        sql = "CREATE TABLE users (id SERIAL PRIMARY KEY);\n" + unparseable_ddl
        with pytest.raises(SchemaParseError, match="2 CREATE TABLE statement.*only 1"):
            parse_schema(sql)

    def test_旧スキーマが解析できないとき旧スキーマのファイル名を添えて中断する(self, tmp_path):
        old = _write_schema(
            tmp_path, "old.sql", "CREATE TABLE t PARTITION OF p FOR VALUES FROM (1) TO (2);"
        )
        new = _write_schema(tmp_path, "new.sql", "CREATE TABLE users (id SERIAL PRIMARY KEY);")
        with pytest.raises(SchemaParseError, match=r"old\.sql"):
            check(old, new)

    def test_新スキーマが解析できないとき新スキーマのファイル名を添えて中断する(self, tmp_path):
        old = _write_schema(tmp_path, "old.sql", "CREATE TABLE users (id SERIAL PRIMARY KEY);")
        new = _write_schema(
            tmp_path, "new.sql", "CREATE TABLE t PARTITION OF p FOR VALUES FROM (1) TO (2);"
        )
        with pytest.raises(SchemaParseError, match=r"new\.sql"):
            check(old, new)


class TestCLIの終了コード:
    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["schema_check"], id="引数0個のとき exit 2"),
            pytest.param(["schema_check", "only_one_path"], id="引数1個のとき exit 2"),
            pytest.param(["schema_check", "a", "b", "c"], id="引数3個のとき exit 2"),
        ],
    )
    def test_引数がold_newの2個でなければexit2にする(self, monkeypatch, argv):
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2

    def test_破壊的変更が無ければexit0でapplyを許可する(self, monkeypatch, tmp_path):
        sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = _write_schema(tmp_path, "old.sql", sql)
        new = _write_schema(tmp_path, "new.sql", sql)
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_破壊的変更があればexit1で止め該当警告を出力する(self, monkeypatch, tmp_path, capsys):
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

    def test_解析できないDDLがあればexit3で止め解析できた件数を出力する(self, monkeypatch, tmp_path, capsys):
        old = _write_schema(tmp_path, "old.sql", "CREATE TABLE users (id SERIAL PRIMARY KEY);")
        new = _write_schema(
            tmp_path,
            "new.sql",
            "CREATE TABLE users (id SERIAL PRIMARY KEY);\n"
            "CREATE TABLE users_2026 PARTITION OF users FOR VALUES FROM (1) TO (2);",
        )
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 3
        assert "2 CREATE TABLE statement(s) present but only 1" in capsys.readouterr().out
