#!/usr/bin/env python3
"""新旧の union SQL を比較し、破壊的変更（テーブル削除・カラム削除）を検出する。

テーブルは所有サービスで修飾した名前で対応付ける。別サービスが同名のテーブルを
持つため、修飾しないと片方の定義がもう片方を隠し、その削除を検出できなくなる。
"""
import re
import sys

from union_format import split_by_source

TABLE_DEFINITION_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?((?:\w+\.)?\w+)\s*\((.*?)\);",
    re.IGNORECASE | re.DOTALL,
)
CREATE_TABLE_KEYWORD_RE = re.compile(r"CREATE\s+TABLE\b", re.IGNORECASE)
CONSTRAINT_KEYWORDS = {
    "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX", "EXCLUDE",
}

EXIT_SAFE = 0
EXIT_DESTRUCTIVE = 1
EXIT_USAGE = 2
EXIT_UNCHECKABLE = 3


class SchemaParseError(Exception):
    """DDL 中の CREATE TABLE を解析できなかったことを表す例外。"""


class TableAttributionError(Exception):
    """テーブルを所有サービス 1 つに対応付けられなかったことを表す例外。"""


def _extract_columns(body: str) -> set[str]:
    """CREATE TABLE 本体からカラム名を抽出します。

    Args:
        body: CREATE TABLE の括弧内の本体テキスト。

    Returns:
        正規化済みのカラム名集合。
    """
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
            # PostgreSQL は引用符なし識別子を小文字へ畳み、引用符付きは大小を保持する。
            columns.add(ident if first_token.startswith('"') else ident.lower())
    return columns


def parse_schema(union_sql: str) -> dict[str, set[str]]:
    """union SQL からテーブル定義をパースし、テーブル名→カラム名集合のマップを返します。

    Args:
        union_sql: fetch-schemas.py が生成した union SQL。

    Returns:
        `<所有サービス>.<テーブル名>` をキーとし、カラム名集合を値とするマップ。

    Raises:
        SchemaParseError: SQL 中の CREATE TABLE に解析できないものがある場合。
        TableAttributionError: 所有サービスを特定できない、または同じ所有サービスで
            テーブル名が重複する場合。
    """
    tables: dict[str, set[str]] = {}
    parsed_count = 0
    for owner, section in split_by_source(union_sql):
        for match in TABLE_DEFINITION_RE.finditer(section):
            parsed_count += 1
            qualified = match.group(1).lower()
            # DDL 側の schema 修飾はサービスごとに書き方が揺れるため、修飾を外して
            # 所有サービスで付け直す
            table_name = qualified.split(".", 1)[1] if "." in qualified else qualified
            if owner is None:
                raise TableAttributionError(
                    f"table {table_name!r} appears before any source header. "
                    "Rebuild the union with db-migrate/fetch-schemas.py so that every "
                    "table can be attributed to the service that owns it."
                )
            key = f"{owner}.{table_name}"
            if key in tables:
                raise TableAttributionError(
                    f"table {key!r} is defined more than once. One definition would "
                    "hide the other, and columns dropped from the hidden one would go "
                    "unreported."
                )
            tables[key] = _extract_columns(match.group(2))

    # 解析できない CREATE TABLE を 0 件と同一視すると、そのテーブルの削除が
    # 破壊的変更として警告されないまま適用されるため、件数の食い違いで中断する
    declared_count = len(CREATE_TABLE_KEYWORD_RE.findall(union_sql))
    if parsed_count != declared_count:
        raise SchemaParseError(
            f"{declared_count} CREATE TABLE statement(s) present but only "
            f"{parsed_count} could be parsed. Unparsed tables would be silently "
            f"excluded from the destructive-change check."
        )
    return tables


def _parse_schema_file(path: str) -> dict[str, set[str]]:
    """スキーマファイルを読み込んでパースします。

    Raises:
        SchemaParseError: 解析できない CREATE TABLE がある場合。どのファイルかを併記する。
        TableAttributionError: テーブルを所有サービスに対応付けられない場合。
            どのファイルかを併記する。
    """
    with open(path) as f:
        union_sql = f.read()
    try:
        return parse_schema(union_sql)
    except SchemaParseError as e:
        raise SchemaParseError(f"{path}: {e}") from e
    except TableAttributionError as e:
        raise TableAttributionError(f"{path}: {e}") from e


def check(old_path: str, new_path: str) -> list[str]:
    """新旧スキーマを比較し、破壊的変更の警告リストを返します。

    Raises:
        SchemaParseError: いずれかのスキーマに解析できない CREATE TABLE がある場合。
        TableAttributionError: いずれかのスキーマでテーブルを所有サービスに
            対応付けられない場合。
    """
    old_schema = _parse_schema_file(old_path)
    new_schema = _parse_schema_file(new_path)

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
    """スキーマ安全性チェックのメインエントリーポイントです。"""
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <old_schema.sql> <new_schema.sql>")
        sys.exit(EXIT_USAGE)

    old_path, new_path = sys.argv[1], sys.argv[2]
    try:
        warnings = check(old_path, new_path)
    except SchemaParseError as e:
        print(f"⚠ Schema safety check: cannot parse schema: {e}")
        print()
        print("The destructive-change check cannot cover tables it failed to parse.")
        print("Extend db-migrate/schema_check.py to handle the DDL form above.")
        sys.exit(EXIT_UNCHECKABLE)
    except TableAttributionError as e:
        print(f"⚠ Schema safety check: cannot attribute a table to its owner: {e}")
        print()
        print("Tables are compared per owning service, so every CREATE TABLE in the")
        print("union must belong to exactly one service listed in schemas.lock.yaml.")
        sys.exit(EXIT_UNCHECKABLE)

    if not warnings:
        print("Schema safety check: OK (no destructive changes)")
        sys.exit(EXIT_SAFE)

    print("⚠ Schema safety check: destructive changes detected!")
    for w in warnings:
        print(f"  - {w}")
    print()
    print("If intentional, re-run the manual workflow with dry_run=true to preview,")
    print("then confirm the changes are safe before applying.")
    sys.exit(EXIT_DESTRUCTIVE)


if __name__ == "__main__":
    main()
