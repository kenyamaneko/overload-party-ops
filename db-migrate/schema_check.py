#!/usr/bin/env python3
"""新旧スキーマを比較し、破壊的変更（テーブル削除・カラム削除）を検出する。"""
import re
import sys

TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+((?:\w+\.)?\w+)\s*\((.*?)\);",
    re.IGNORECASE | re.DOTALL,
)
CONSTRAINT_KEYWORDS = {
    "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX", "EXCLUDE",
}


def _extract_columns(body: str) -> set[str]:
    """CREATE TABLE 本体からカラム名を抽出する。"""
    columns: set[str] = set()
    for line in body.split(","):
        line = line.strip()
        if not line:
            continue
        first_token = line.split()[0] if line.split() else ""
        ident = first_token.strip('"')
        if ident.upper() in CONSTRAINT_KEYWORDS:
            continue
        if re.match(r"^\"?\w+\"?$", first_token):
            columns.add(ident.lower())
    return columns


def parse_schema(sql: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for match in TABLE_RE.finditer(sql):
        qualified = match.group(1).lower()
        # schema-qualified name から unqualified 部分のみ取り出す（旧 public スキーマ時代の
        # DATA_DESIGN.md や schema dump と比較できるように unqualified をキーにする）
        table_name = qualified.split(".", 1)[1] if "." in qualified else qualified
        body = match.group(2)
        tables[table_name] = _extract_columns(body)
    return tables


def check(old_path: str, new_path: str) -> list[str]:
    with open(old_path) as f:
        old_schema = parse_schema(f.read())
    with open(new_path) as f:
        new_schema = parse_schema(f.read())

    warnings: list[str] = []

    dropped_tables = set(old_schema) - set(new_schema)
    for t in sorted(dropped_tables):
        warnings.append(f"DROP TABLE: {t}")

    for table in sorted(set(old_schema) & set(new_schema)):
        dropped_cols = old_schema[table] - new_schema[table]
        for col in sorted(dropped_cols):
            warnings.append(f"DROP COLUMN: {table}.{col}")

    return warnings


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <old_schema.sql> <new_schema.sql>")
        sys.exit(2)

    old_path, new_path = sys.argv[1], sys.argv[2]
    warnings = check(old_path, new_path)

    if not warnings:
        print("Schema safety check: OK (no destructive changes)")
        sys.exit(0)

    print("⚠ Schema safety check: destructive changes detected!")
    for w in warnings:
        print(f"  - {w}")
    print()
    print("If intentional, re-run the manual workflow with dry_run=true to preview,")
    print("then confirm the changes are safe before applying.")
    sys.exit(1)


if __name__ == "__main__":
    main()
