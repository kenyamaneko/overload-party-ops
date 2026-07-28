#!/usr/bin/env python3
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parent / "fetch-schemas.py"
_spec = importlib.util.spec_from_file_location("fetch_schemas", MODULE_PATH)
fetch_schemas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_schemas)


def _source(name: str, schema: str = "CREATE TABLE t ();", seeds=()) -> dict:
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


class Testマスタデータ投入SQLの結合:
    def test_複数サービスのseedが列挙順に並ぶ(self):
        sources = [
            _source("card", seeds=[("db/seed/a.sql", "INSERT INTO card.a;"),
                                   ("db/seed/b.sql", "INSERT INTO card.b;")]),
            _source("shop", seeds=[("db/seed/c.sql", "INSERT INTO shop.c;")]),
        ]

        union = fetch_schemas.render_seed_union(sources)

        assert union.index("INSERT INTO card.a;") < union.index("INSERT INTO card.b;")
        assert union.index("INSERT INTO card.b;") < union.index("INSERT INTO shop.c;")

    def test_seedを持たないサービスは結合結果に現れない(self):
        sources = [
            _source("account"),
            _source("card", seeds=[("db/seed/a.sql", "INSERT INTO card.a;")]),
        ]

        union = fetch_schemas.render_seed_union(sources)

        assert "INSERT INTO card.a;" in union
        assert "account" not in union

    def test_seedが1件も無いとき生成物の見出しだけになる(self):
        union = fetch_schemas.render_seed_union([_source("account")])

        assert "seed_union.sql" in union
        assert "INSERT" not in union

    def test_取得元のリポジトリと参照とパスが出力に残る(self):
        sources = [_source("card", seeds=[("db/seed/a.sql", "INSERT INTO card.a;")])]

        union = fetch_schemas.render_seed_union(sources)

        assert "kenyamaneko/overload-party-card@main" in union
        assert "db/seed/a.sql" in union


class TestスキーマDDLの結合:
    def test_seedのSQLは混ざらない(self):
        sources = [_source("card", schema="CREATE TABLE card.card_definitions ();",
                           seeds=[("db/seed/a.sql", "INSERT INTO card.a;")])]

        union = fetch_schemas.render_union(sources)

        assert "CREATE TABLE card.card_definitions ();" in union
        assert "INSERT INTO card.a;" not in union


class Test取得対象の決定:
    def test_seedの出力を求めないとき取得するのはDDLだけになる(self, tmp_path, monkeypatch):
        requested = []

        def fake_clone(repo, ref, file_paths, dest, token):
            requested.extend(file_paths)
            paths = []
            for file_path in file_paths:
                target = dest / file_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("SQL", encoding="utf-8")
                paths.append(target)
            return paths

        monkeypatch.setattr(fetch_schemas, "_clone_sparse", fake_clone)
        lock = {"schemas": [{"name": "card", "repo": "kenyamaneko/overload-party-card",
                             "path": "db/schema.sql", "ref": "main",
                             "seeds": ["db/seed/a.sql"]}]}

        sources = fetch_schemas.fetch_sources(lock, tmp_path, None, None, with_seeds=False)

        assert requested == ["db/schema.sql"]
        assert sources[0]["seeds"] == []

    def test_seedの出力を求めるときDDLと同じ取得でseedも取る(self, tmp_path, monkeypatch):
        requested = []

        def fake_clone(repo, ref, file_paths, dest, token):
            requested.extend(file_paths)
            paths = []
            for file_path in file_paths:
                target = dest / file_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"SQL {file_path}", encoding="utf-8")
                paths.append(target)
            return paths

        monkeypatch.setattr(fetch_schemas, "_clone_sparse", fake_clone)
        lock = {"schemas": [{"name": "card", "repo": "kenyamaneko/overload-party-card",
                             "path": "db/schema.sql", "ref": "main",
                             "seeds": ["db/seed/a.sql"]}]}

        sources = fetch_schemas.fetch_sources(lock, tmp_path, None, None, with_seeds=True)

        assert requested == ["db/schema.sql", "db/seed/a.sql"]
        assert sources[0]["seeds"] == [("db/seed/a.sql", "SQL db/seed/a.sql")]
