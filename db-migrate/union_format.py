#!/usr/bin/env python3
"""union SQL のサービス区分の書式。fetch-schemas.py が書き、schema_check.py が読む。"""
import re

BANNER = "-- " + "=" * 77

_SOURCE_HEADER_RE = re.compile(r"^-- \[(\w+)\]  source: \S+@\S+  path: \S+$", re.MULTILINE)


def render_source_header(name: str, repo: str, ref: str, path: str) -> list[str]:
    """union に埋め込むサービス区分の見出しを組み立てます。

    Args:
        name: DDL を所有するサービス名。
        repo: 取得元リポジトリ。
        ref: 取得した ref。
        path: 取得したファイルのリポジトリ内パス。

    Returns:
        見出しを構成する行。
    """
    return [BANNER, f"-- [{name}]  source: {repo}@{ref}  path: {path}", BANNER]


def split_by_source(union_sql: str) -> list[tuple[str | None, str]]:
    """union SQL をサービス区分の見出しで分割します。

    Args:
        union_sql: fetch-schemas.py が生成した union SQL。

    Returns:
        (所有サービス名, その区分の SQL) を出現順に並べたもの。最初の見出しより前の
        領域は所有サービスを特定できないため、サービス名を None として先頭に含む。
    """
    sections: list[tuple[str | None, str]] = []
    owner: str | None = None
    section_start = 0
    for match in _SOURCE_HEADER_RE.finditer(union_sql):
        sections.append((owner, union_sql[section_start:match.start()]))
        owner = match.group(1)
        section_start = match.end()
    sections.append((owner, union_sql[section_start:]))
    return sections
