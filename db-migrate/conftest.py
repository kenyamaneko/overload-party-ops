#!/usr/bin/env python3
"""db-migrate のテストで共有する組み立て手順。"""
from union_format import render_source_header


def build_union(*sections: tuple[str, str]) -> str:
    """サービスごとの DDL を fetch-schemas.py と同じ書式の union SQL に組み立てます。

    Args:
        sections: (所有サービス名, そのサービスの DDL) の組。

    Returns:
        サービス区分の見出しを挟んで結合した union SQL。
    """
    chunks: list[str] = []
    for name, ddl in sections:
        chunks.extend(
            render_source_header(name, f"kenyamaneko/overload-party-{name}", "main", "db/schema.sql")
        )
        chunks.append(ddl)
    return "\n".join(chunks) + "\n"
