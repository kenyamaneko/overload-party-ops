#!/usr/bin/env python3
import pytest
from conftest import build_union
from schema_check import (
    EmptyBaselineError,
    SchemaParseError,
    TableAttributionError,
    check,
    main,
    parse_schema,
)


def _write_schema(tmp_path, name: str, content: str) -> str:
    """SQL を一時ファイルに書き出してパスを返します。

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


def _write_union(tmp_path, name: str, *sections: tuple[str, str]) -> str:
    """サービスごとの DDL を union SQL にまとめて一時ファイルへ書き出します。

    Args:
        tmp_path: pytest が用意する一時ディレクトリ。
        name: 作成するファイル名。
        sections: (所有サービス名, そのサービスの DDL) の組。

    Returns:
        作成した一時ファイルの絶対パス文字列。
    """
    return _write_schema(tmp_path, name, build_union(*sections))


class TestスキーマDDLのパース:
    def test_基本的なテーブルのカラムを抽出する(self):
        tables = parse_schema(build_union(("account", """
        CREATE TABLE account.users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email TEXT
        );
        """)))
        assert tables["account.users"] == {"id", "name", "email"}

    def test_IF_NOT_EXISTS付きで定義されたテーブルのカラムを抽出する(self):
        tables = parse_schema(build_union(("news", """
        CREATE TABLE IF NOT EXISTS news.news_articles (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT
        );
        """)))
        assert tables["news.news_articles"] == {"id", "title", "body"}

    def test_複数テーブルをそれぞれ抽出する(self):
        tables = parse_schema(build_union(("battle", """
        CREATE TABLE battle.games (
            id SERIAL PRIMARY KEY,
            status VARCHAR(20)
        );
        CREATE TABLE battle.game_npcs (
            id SERIAL PRIMARY KEY,
            game_id INTEGER
        );
        """)))
        assert set(tables) == {"battle.games", "battle.game_npcs"}

    def test_制約キーワードはカラムに含めない(self):
        tables = parse_schema(build_union(("shop", """
        CREATE TABLE shop.orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE (user_id)
        );
        """)))
        assert tables["shop.orders"] == {"id", "user_id"}

    def test_CHECK制約内のカンマでカラムを誤分割しない(self):
        tables = parse_schema(build_union(("account", """
        CREATE TABLE account.player_factions (
            id SERIAL PRIMARY KEY,
            faction VARCHAR(20) NOT NULL CHECK (faction IN ('SHE', 'Tenki', 'Sugar', 'Tuners'))
        );
        """)))
        assert tables["account.player_factions"] == {"id", "faction"}

    def test_空スキーマは空dictになる(self):
        assert parse_schema("") == {}

    def test_大文字小文字を問わずパースする(self):
        tables = parse_schema(build_union(("account", """
        create table account.Users (
            ID serial primary key,
            Name varchar(255)
        );
        """)))
        assert tables["account.users"] == {"id", "name"}


class Test表制約とカラム名の区別:
    @pytest.mark.parametrize(
        ("element", "expected"),
        [
            pytest.param(
                "index INTEGER",
                {"id", "index"},
                id="index INTEGER があるとき、index をカラムとして読み取る",
            ),
            pytest.param(
                "exclude BOOLEAN",
                {"id", "exclude"},
                id="exclude BOOLEAN があるとき、exclude をカラムとして読み取る",
            ),
            pytest.param(
                "exclude",
                {"id", "exclude"},
                id="型を伴わない exclude があるとき、exclude をカラムとして読み取る",
            ),
            pytest.param(
                "EXCLUDE USING gist (id WITH =)",
                {"id"},
                id="索引方式を伴う除外制約があるとき、カラムは id だけになる",
            ),
            pytest.param(
                "EXCLUDE (id WITH =)",
                {"id"},
                id="索引方式を省いた除外制約があるとき、カラムは id だけになる",
            ),
        ],
    )
    def test_カラム定義と表制約を読み分ける(self, element, expected):
        tables = parse_schema(build_union(("battle", f"CREATE TABLE battle.rooms (id INTEGER, {element});")))
        assert tables["battle.rooms"] == expected

    def test_indexとexcludeという語のカラムを削除するとDROP_COLUMN警告になる(self, tmp_path):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("battle", "CREATE TABLE battle.rooms (id INTEGER, index INTEGER, exclude BOOLEAN);"),
        )
        new = _write_union(tmp_path, "new.sql", ("battle", "CREATE TABLE battle.rooms (id INTEGER);"))
        assert check(old, new) == [
            "DROP COLUMN: battle.rooms.exclude",
            "DROP COLUMN: battle.rooms.index",
        ]

    def test_除外制約を消してもカラム削除としては警告しない(self, tmp_path):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("battle", "CREATE TABLE battle.rooms (id INTEGER, EXCLUDE USING gist (id WITH =));"),
        )
        new = _write_union(tmp_path, "new.sql", ("battle", "CREATE TABLE battle.rooms (id INTEGER);"))
        assert check(old, new) == []


class Testコメントを含むDDLのパース:
    def test_全てのカラム定義に行末コメントが付いているとき全カラムを抽出する(self):
        tables = parse_schema(build_union(("shop", """
        CREATE TABLE shop.outbox_events (
          event_id          UUID NOT NULL,                        -- payload 内 eventId と一致
          event_type        VARCHAR(100) NOT NULL,                -- 論理イベント種別
          payload           JSONB NOT NULL,                       -- イベント本体
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),   -- enqueue 日時
          published_at      TIMESTAMPTZ,                          -- NULL = 未配信
          PRIMARY KEY (event_id)
        );
        """)))
        assert tables["shop.outbox_events"] == {
            "event_id", "event_type", "payload", "created_at", "published_at",
        }

    def test_行末コメントにカンマと括弧が含まれるとき後続のカラムも抽出する(self):
        tables = parse_schema(build_union(("account", """
        CREATE TABLE account.player_factions (
          player_id UUID NOT NULL,  -- 陣営名 (SHE, Tenki, Sugar)
          faction   VARCHAR(20)     -- 所属陣営（NULL: 全陣営共通）
        );
        """)))
        assert tables["account.player_factions"] == {"player_id", "faction"}

    def test_ブロックコメントがカラム定義の間にあるとき前後のカラムを抽出する(self):
        tables = parse_schema(build_union(("card", """
        CREATE TABLE card.decks (
          deck_id BIGINT NOT NULL,
          /* 複数行にわたる
             説明 */
          name TEXT NOT NULL
        );
        """)))
        assert tables["card.decks"] == {"deck_id", "name"}

    def test_入れ子のブロックコメントがカラム定義の間にあるとき前後のカラムを抽出する(self):
        tables = parse_schema(build_union(("card", """
        CREATE TABLE card.decks (
          deck_id BIGINT NOT NULL,
          /* 説明 /* 補足 */ の続き */
          name TEXT NOT NULL
        );
        """)))
        assert tables["card.decks"] == {"deck_id", "name"}

    def test_初期値の文字列に含まれるハイフン2個をコメントとして扱わない(self):
        tables = parse_schema(build_union(("news", """
        CREATE TABLE news.news_articles (
          slug  TEXT NOT NULL DEFAULT '--',
          title TEXT NOT NULL
        );
        """)))
        assert tables["news.news_articles"] == {"slug", "title"}

    def test_コメントにCREATE_TABLEの語があるときテーブル数の食い違いとしない(self):
        tables = parse_schema(build_union(("support", """
        -- 問い合わせの CREATE TABLE は support スキーマに置く
        CREATE TABLE support.inquiries (inquiry_id BIGINT NOT NULL, body TEXT);
        """)))
        assert set(tables) == {"support.inquiries"}

    def test_関数本体にCREATE_TABLEがあるときテーブル数の食い違いとしない(self):
        tables = parse_schema(build_union(("battle", """
        CREATE FUNCTION battle.bootstrap() RETURNS void AS $$
        BEGIN
          CREATE TABLE battle.scratch (id INT);
        END;
        $$ LANGUAGE plpgsql;
        CREATE TABLE battle.games (game_id UUID NOT NULL, status TEXT);
        """)))
        assert set(tables) == {"battle.games"}


class Test同名テーブルの所有サービスによる区別:
    def test_別のサービスが同名のテーブルを持つときそれぞれ独立したテーブルとして読み取る(self):
        tables = parse_schema(build_union(
            ("shop", "CREATE TABLE shop.outbox_events (id UUID PRIMARY KEY, payload JSONB);"),
            ("scenario", "CREATE TABLE scenario.outbox_events (id UUID PRIMARY KEY);"),
        ))
        assert tables == {
            "shop.outbox_events": {"id", "payload"},
            "scenario.outbox_events": {"id"},
        }

    @pytest.mark.parametrize(
        ("new_sections", "expected"),
        [
            pytest.param(
                (
                    ("shop", "CREATE TABLE shop.outbox_events (id UUID PRIMARY KEY);"),
                    ("scenario",
                     "CREATE TABLE scenario.outbox_events (id UUID PRIMARY KEY, aggregate_id UUID);"),
                ),
                ["DROP COLUMN: shop.outbox_events.payload"],
                id="先に結合される shop からカラムを削除したとき、shop.outbox_events のカラム削除として報告される",
            ),
            pytest.param(
                (
                    ("shop", "CREATE TABLE shop.outbox_events (id UUID PRIMARY KEY, payload JSONB);"),
                    ("scenario", "CREATE TABLE scenario.outbox_events (id UUID PRIMARY KEY);"),
                ),
                ["DROP COLUMN: scenario.outbox_events.aggregate_id"],
                id="後に結合される scenario からカラムを削除したとき、scenario.outbox_events のカラム削除として報告される",
            ),
        ],
    )
    def test_同名テーブルを持つ2つのサービスの片方からカラムを削除する(self, tmp_path, new_sections, expected):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("shop", "CREATE TABLE shop.outbox_events (id UUID PRIMARY KEY, payload JSONB);"),
            ("scenario", "CREATE TABLE scenario.outbox_events (id UUID PRIMARY KEY, aggregate_id UUID);"),
        )
        new = _write_union(tmp_path, "new.sql", *new_sections)
        assert check(old, new) == expected

    def test_同名テーブルを持つ一方のサービスがテーブルを消したときそのサービスの削除だけを報告する(self, tmp_path):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("account", "CREATE TABLE account.processed_events (event_id UUID PRIMARY KEY);"),
            ("card", "CREATE TABLE card.processed_events (event_id UUID PRIMARY KEY);"),
        )
        new = _write_union(
            tmp_path,
            "new.sql",
            ("account", ""),
            ("card", "CREATE TABLE card.processed_events (event_id UUID PRIMARY KEY);"),
        )
        assert check(old, new) == ["DROP TABLE: account.processed_events"]


class Test破壊的変更の検出:
    def test_変更が無ければ警告は空(self, tmp_path):
        ddl = "CREATE TABLE account.users (id SERIAL PRIMARY KEY, name TEXT);"
        old = _write_union(tmp_path, "old.sql", ("account", ddl))
        new = _write_union(tmp_path, "new.sql", ("account", ddl))
        assert check(old, new) == []

    def test_カラム追加は警告しない(self, tmp_path):
        old = _write_union(tmp_path, "old.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        new = _write_union(
            tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY, name TEXT);")
        )
        assert check(old, new) == []

    def test_カラム削除はDROP_COLUMN警告になる(self, tmp_path):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY, name TEXT, email TEXT);"),
        )
        new = _write_union(
            tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY, name TEXT);")
        )
        assert check(old, new) == ["DROP COLUMN: account.users.email"]

    def test_テーブル削除はDROP_TABLE警告になる(self, tmp_path):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("account", """
            CREATE TABLE account.users (id SERIAL PRIMARY KEY);
            CREATE TABLE account.logs (id SERIAL PRIMARY KEY);
            """),
        )
        new = _write_union(tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        assert check(old, new) == ["DROP TABLE: account.logs"]

    def test_行末コメントの付いたカラムを削除するとDROP_COLUMN警告になる(self, tmp_path):
        old = _write_union(tmp_path, "old.sql", ("shop", """
        CREATE TABLE shop.outbox_events (
          event_id     UUID NOT NULL,   -- payload 内 eventId と一致
          last_error   TEXT,            -- 直近エラーメッセージ
          PRIMARY KEY (event_id)
        );
        """))
        new = _write_union(tmp_path, "new.sql", ("shop", """
        CREATE TABLE shop.outbox_events (
          event_id     UUID NOT NULL,   -- payload 内 eventId と一致
          PRIMARY KEY (event_id)
        );
        """))
        assert check(old, new) == ["DROP COLUMN: shop.outbox_events.last_error"]

    def test_IF_NOT_EXISTS付きで定義されたテーブルの削除もDROP_TABLE警告になる(self, tmp_path):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("news", """
            CREATE TABLE IF NOT EXISTS news.news_articles (id SERIAL PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS news.news_article_translations (id SERIAL PRIMARY KEY);
            """),
        )
        new = _write_union(
            tmp_path, "new.sql", ("news", "CREATE TABLE IF NOT EXISTS news.news_articles (id SERIAL PRIMARY KEY);")
        )
        assert check(old, new) == ["DROP TABLE: news.news_article_translations"]

    def test_IF_NOT_EXISTS付きで定義されたテーブルのカラム削除もDROP_COLUMN警告になる(self, tmp_path):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("support", "CREATE TABLE IF NOT EXISTS support.inquiries (id SERIAL PRIMARY KEY, body TEXT);"),
        )
        new = _write_union(
            tmp_path, "new.sql", ("support", "CREATE TABLE IF NOT EXISTS support.inquiries (id SERIAL PRIMARY KEY);")
        )
        assert check(old, new) == ["DROP COLUMN: support.inquiries.body"]

    def test_テーブル追加は警告しない(self, tmp_path):
        old = _write_union(tmp_path, "old.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        new = _write_union(
            tmp_path,
            "new.sql",
            ("account", """
            CREATE TABLE account.users (id SERIAL PRIMARY KEY);
            CREATE TABLE account.logs (id SERIAL PRIMARY KEY, message TEXT);
            """),
        )
        assert check(old, new) == []


class TestDDL側のschema修飾の畳み込み:
    @pytest.mark.parametrize(
        "qualifier",
        [
            pytest.param("", id="無修飾のとき shop.users キーになる"),
            pytest.param("shop.", id="所有サービスと同じ shop. 修飾でも shop.users キーになる"),
            pytest.param("public.", id="public. 修飾でも shop.users キーになる"),
            pytest.param("MySchema.", id="MySchema. 修飾でも shop.users キーになる"),
        ],
    )
    def test_DDL側の修飾を外し所有サービスで修飾し直したテーブル名をキーにする(self, qualifier):
        tables = parse_schema(
            build_union(("shop", f"CREATE TABLE {qualifier}users (id SERIAL PRIMARY KEY, name TEXT);"))
        )
        assert set(tables) == {"shop.users"}
        assert tables["shop.users"] == {"id", "name"}


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
        tables = parse_schema(build_union(("account", f"CREATE TABLE t ({column_definition});")))
        assert tables["account.t"] == {expected}


class Test複数破壊的変更の検出:
    def test_複数テーブル削除と複数カラム削除が全て整列順で報告される(self, tmp_path):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("card", """
            CREATE TABLE card.one (id SERIAL PRIMARY KEY, a TEXT, b TEXT, c TEXT);
            CREATE TABLE card.two (id SERIAL PRIMARY KEY);
            CREATE TABLE card.three (id SERIAL PRIMARY KEY);
            """),
        )
        new = _write_union(tmp_path, "new.sql", ("card", "CREATE TABLE card.one (id SERIAL PRIMARY KEY);"))
        assert check(old, new) == [
            "DROP TABLE: card.three",
            "DROP TABLE: card.two",
            "DROP COLUMN: card.one.a",
            "DROP COLUMN: card.one.b",
            "DROP COLUMN: card.one.c",
        ]

    @pytest.mark.parametrize(
        ("old_qualifier", "new_qualifier"),
        [
            pytest.param("shop.", "", id="shop. から無修飾でも DROP としない"),
            pytest.param("", "shop.", id="無修飾から shop. でも DROP としない"),
            pytest.param("shop.", "public.", id="shop. から public. でも DROP としない"),
        ],
    )
    def test_DDL側のschema修飾が変わっただけでは破壊的変更にしない(self, tmp_path, old_qualifier, new_qualifier):
        old = _write_union(
            tmp_path, "old.sql", ("shop", f"CREATE TABLE {old_qualifier}users (id SERIAL PRIMARY KEY, name TEXT);")
        )
        new = _write_union(
            tmp_path, "new.sql", ("shop", f"CREATE TABLE {new_qualifier}users (id SERIAL PRIMARY KEY, name TEXT);")
        )
        assert check(old, new) == []

    def test_修飾付きテーブルの削除は所有サービスで修飾した名前で警告される(self, tmp_path):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("shop", """
            CREATE TABLE public.users (id SERIAL PRIMARY KEY);
            CREATE TABLE public.logs (id SERIAL PRIMARY KEY);
            """),
        )
        new = _write_union(tmp_path, "new.sql", ("shop", "CREATE TABLE public.users (id SERIAL PRIMARY KEY);"))
        assert check(old, new) == ["DROP TABLE: shop.logs"]


class Test所有サービスを特定できないテーブルの検出:
    def test_サービス区分の見出しより前にテーブルがあるとき中断する(self):
        with pytest.raises(TableAttributionError, match="before any source header"):
            parse_schema("CREATE TABLE users (id SERIAL PRIMARY KEY);")

    def test_同じサービスがschema修飾違いで同名のテーブルを定義するとき中断する(self):
        union = build_union(("shop", """
        CREATE TABLE shop.products (id UUID PRIMARY KEY);
        CREATE TABLE public.products (id UUID PRIMARY KEY);
        """))
        with pytest.raises(TableAttributionError, match="shop.products' is defined more than once"):
            parse_schema(union)

    def test_比較元の所有サービスが特定できないとき比較元のファイル名を添えて中断する(self, tmp_path):
        old = _write_schema(tmp_path, "old.sql", "CREATE TABLE users (id SERIAL PRIMARY KEY);")
        new = _write_union(tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        with pytest.raises(TableAttributionError, match=r"old\.sql"):
            check(old, new)


class Test解析できないDDLの検出:
    @pytest.mark.parametrize(
        "unparseable_ddl",
        [
            pytest.param(
                "CREATE TABLE battle.games_2026 PARTITION OF battle.games "
                "FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');",
                id="パーティションの定義が解析できないとき、中断する",
            ),
            pytest.param(
                "CREATE TABLE battle.active_users AS SELECT id FROM battle.games;",
                id="問い合わせ結果からの定義が解析できないとき、中断する",
            ),
            pytest.param(
                "CREATE UNLOGGED TABLE battle.scratch (id SERIAL PRIMARY KEY);",
                id="ログを取らないテーブルの定義が解析できないとき、中断する",
            ),
            pytest.param(
                "CREATE TEMP TABLE battle.scratch (id SERIAL PRIMARY KEY);",
                id="TEMP 指定の一時テーブルの定義が解析できないとき、中断する",
            ),
            pytest.param(
                "CREATE TEMPORARY TABLE battle.scratch (id SERIAL PRIMARY KEY);",
                id="TEMPORARY 指定の一時テーブルの定義が解析できないとき、中断する",
            ),
            pytest.param(
                "CREATE GLOBAL TEMPORARY TABLE battle.scratch (id SERIAL PRIMARY KEY);",
                id="GLOBAL 指定の一時テーブルの定義が解析できないとき、中断する",
            ),
            pytest.param(
                "CREATE LOCAL TEMP TABLE battle.scratch (id SERIAL PRIMARY KEY);",
                id="LOCAL 指定の一時テーブルの定義が解析できないとき、中断する",
            ),
            pytest.param(
                "CREATE FOREIGN TABLE battle.remote_games (id INTEGER) SERVER remote;",
                id="外部テーブルの定義が解析できないとき、中断する",
            ),
        ],
    )
    def test_解析できないCREATE_TABLEが混ざるとき件数の食い違いを理由に中断する(self, unparseable_ddl):
        union = build_union(("battle", "CREATE TABLE battle.games (id SERIAL PRIMARY KEY);\n" + unparseable_ddl))
        with pytest.raises(SchemaParseError, match="2 CREATE TABLE statement.*only 1"):
            parse_schema(union)

    def test_比較元が解析できないとき比較元のファイル名を添えて中断する(self, tmp_path):
        old = _write_union(
            tmp_path, "old.sql", ("battle", "CREATE TABLE t PARTITION OF p FOR VALUES FROM (1) TO (2);")
        )
        new = _write_union(tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        with pytest.raises(SchemaParseError, match=r"old\.sql"):
            check(old, new)

    def test_適用するスキーマが解析できないとき適用するスキーマのファイル名を添えて中断する(self, tmp_path):
        old = _write_union(tmp_path, "old.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        new = _write_union(
            tmp_path, "new.sql", ("battle", "CREATE TABLE t PARTITION OF p FOR VALUES FROM (1) TO (2);")
        )
        with pytest.raises(SchemaParseError, match=r"new\.sql"):
            check(old, new)


class Testカラムを読み取れないDDLの検出:
    def test_カラムを1つも定義していないテーブルがあるとき中断する(self):
        union = build_union(("battle", "CREATE TABLE battle.games ();"))
        with pytest.raises(SchemaParseError, match="'battle.games' yielded no columns"):
            parse_schema(union)

    def test_カラム名にも表制約にも分類できない要素があるとき中断する(self):
        union = build_union(("battle", "CREATE TABLE battle.games (game_id UUID, 'status' TEXT);"))
        with pytest.raises(SchemaParseError, match="neither a column name nor a table constraint"):
            parse_schema(union)

    def test_カラムリストの括弧が閉じられないとき中断する(self):
        union = build_union(("battle", "CREATE TABLE battle.games (game_id UUID NOT NULL, status TEXT;"))
        with pytest.raises(SchemaParseError, match="unbalanced parentheses"):
            parse_schema(union)

    def test_文字列リテラルが閉じられないとき中断する(self):
        union = build_union(("battle", "CREATE TABLE battle.games (status TEXT NOT NULL DEFAULT 'idle);"))
        with pytest.raises(SchemaParseError, match="unterminated ' quoted text"):
            parse_schema(union)

    def test_ブロックコメントが閉じられないとき中断する(self):
        union = build_union(("battle", "CREATE TABLE battle.games (game_id UUID); /* 続き"))
        with pytest.raises(SchemaParseError, match="unterminated block comment"):
            parse_schema(union)


class TestCLIの終了コード:
    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(["schema_check"], id="引数0個のとき exit 2"),
            pytest.param(["schema_check", "only_one_path"], id="引数1個のとき exit 2"),
            pytest.param(["schema_check", "a", "b", "c"], id="引数3個のとき exit 2"),
        ],
    )
    def test_引数が比較元と適用するスキーマの2個でなければexit2にする(self, monkeypatch, argv):
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2

    def test_破壊的変更が無ければexit0でapplyを許可する(self, monkeypatch, tmp_path):
        ddl = "CREATE TABLE account.users (id SERIAL PRIMARY KEY, name TEXT);"
        old = _write_union(tmp_path, "old.sql", ("account", ddl))
        new = _write_union(tmp_path, "new.sql", ("account", ddl))
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_破壊的変更があればexit1で止め該当警告を出力する(self, monkeypatch, tmp_path, capsys):
        old = _write_union(
            tmp_path,
            "old.sql",
            ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY, name TEXT, email TEXT);"),
        )
        new = _write_union(
            tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY, name TEXT);")
        )
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "DROP COLUMN: account.users.email" in capsys.readouterr().out

    def test_解析できないDDLがあればexit3で止め解析できた件数を出力する(self, monkeypatch, tmp_path, capsys):
        old = _write_union(tmp_path, "old.sql", ("battle", "CREATE TABLE battle.games (id SERIAL PRIMARY KEY);"))
        new = _write_union(
            tmp_path,
            "new.sql",
            ("battle", "CREATE TABLE battle.games (id SERIAL PRIMARY KEY);\n"
                       "CREATE TABLE battle.games_2026 PARTITION OF battle.games FOR VALUES FROM (1) TO (2);"),
        )
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 3
        assert "2 CREATE TABLE statement(s) present but only 1" in capsys.readouterr().out

    def test_カラムを読み取れないテーブルがあればexit3で止め該当テーブルを出力する(self, monkeypatch, tmp_path, capsys):
        old = _write_union(tmp_path, "old.sql", ("battle", "CREATE TABLE battle.games (game_id UUID);"))
        new = _write_union(tmp_path, "new.sql", ("battle", "CREATE TABLE battle.games ();"))
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 3
        assert "table 'battle.games' yielded no columns" in capsys.readouterr().out

    def test_所有サービスを特定できないテーブルがあればexit3で止め該当テーブルを出力する(self, monkeypatch, tmp_path, capsys):
        old = _write_union(tmp_path, "old.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        new = _write_schema(tmp_path, "new.sql", "CREATE TABLE users (id SERIAL PRIMARY KEY);")
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 3
        assert "table 'users' appears before any source header" in capsys.readouterr().out


class Test比較元が未記録のときの扱い:
    def test_比較元が無く初回と申告していないときexit4で止め初回である旨を出力する(self, monkeypatch, tmp_path, capsys):
        missing = str(tmp_path / "not-recorded.sql")
        new = _write_union(tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        monkeypatch.setattr("sys.argv", ["schema_check", missing, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 4
        assert "no schema union has been recorded as applied yet" in capsys.readouterr().out

    def test_比較元が無く初回と申告したときexit0で検査していない旨を出力する(self, monkeypatch, tmp_path, capsys):
        missing = str(tmp_path / "not-recorded.sql")
        new = _write_union(tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        monkeypatch.setattr("sys.argv", ["schema_check", "--bootstrap-baseline", missing, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        assert "NOT PERFORMED" in capsys.readouterr().out

    def test_比較元が無く初回と申告したとき解析できないDDLがあればexit3で止め解析できた件数を出力する(
        self, monkeypatch, tmp_path, capsys
    ):
        missing = str(tmp_path / "not-recorded.sql")
        new = _write_union(
            tmp_path,
            "new.sql",
            ("battle", "CREATE TABLE battle.games (id SERIAL PRIMARY KEY);\n"
                       "CREATE TABLE battle.games_2026 PARTITION OF battle.games FOR VALUES FROM (1) TO (2);"),
        )
        monkeypatch.setattr("sys.argv", ["schema_check", "--bootstrap-baseline", missing, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 3
        assert "2 CREATE TABLE statement(s) present but only 1" in capsys.readouterr().out

    def test_比較元が無く初回と申告したとき適用するunionにテーブルが1つも無ければexit3で止め比較できなくなる旨を出力する(
        self, monkeypatch, tmp_path, capsys
    ):
        missing = str(tmp_path / "not-recorded.sql")
        new = _write_schema(tmp_path, "new.sql", "")
        monkeypatch.setattr("sys.argv", ["schema_check", "--bootstrap-baseline", missing, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 3
        assert "the schema union to apply holds no table" in capsys.readouterr().out

    def test_比較元があるのに初回と申告したときexit2で止め記録済みである旨を出力する(self, monkeypatch, tmp_path, capsys):
        old = _write_union(
            tmp_path, "old.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY, email TEXT);")
        )
        new = _write_union(tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        monkeypatch.setattr("sys.argv", ["schema_check", "--bootstrap-baseline", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
        assert "a schema union is already recorded" in capsys.readouterr().out


class Test比較元にテーブルが無いときの扱い:
    def test_比較元にテーブルが1つも無いとき中断する(self, tmp_path):
        old = _write_schema(tmp_path, "old.sql", "")
        new = _write_union(tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        with pytest.raises(EmptyBaselineError, match="the baseline holds no table"):
            check(old, new)

    def test_比較元にテーブルが1つも無いときexit3で止め比較できない旨を出力する(self, monkeypatch, tmp_path, capsys):
        old = _write_schema(tmp_path, "old.sql", "")
        new = _write_union(tmp_path, "new.sql", ("account", "CREATE TABLE account.users (id SERIAL PRIMARY KEY);"))
        monkeypatch.setattr("sys.argv", ["schema_check", old, new])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 3
        assert "the baseline holds no table" in capsys.readouterr().out
