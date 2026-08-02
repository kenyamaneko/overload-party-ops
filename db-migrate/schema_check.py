#!/usr/bin/env python3
"""環境に適用済みの union SQL と、これから適用する union SQL を比較し、破壊的変更
（テーブル削除・カラム削除）を検出する。

テーブルは所有サービスで修飾した名前で対応付ける。別サービスが同名のテーブルを
持つため、修飾しないと片方の定義がもう片方を隠し、その削除を検出できなくなる。
"""
import argparse
import os
import re
import sys

from union_format import split_by_source

TABLE_HEADER_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?((?:\w+\.)?\w+)\s*\(",
    re.IGNORECASE,
)
CREATE_TABLE_KEYWORD_RE = re.compile(
    r"CREATE\s+(?:(?:(?:GLOBAL|LOCAL)\s+)?(?:TEMPORARY|TEMP)\s+|UNLOGGED\s+|FOREIGN\s+)?TABLE\b",
    re.IGNORECASE,
)
DOLLAR_QUOTE_RE = re.compile(r"\$(?:[A-Za-z_]\w*)?\$")
COLUMN_IDENTIFIER_RE = re.compile(r'^(?:"[^"]+"|\w+)$')
CONSTRAINT_KEYWORDS = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}
EXCLUDE_CONSTRAINT_KEYWORD = "EXCLUDE"
EXCLUDE_INDEX_METHOD_KEYWORD = "USING"

EXIT_SAFE = 0
EXIT_DESTRUCTIVE = 1
EXIT_USAGE = 2
EXIT_UNCHECKABLE = 3
EXIT_NO_BASELINE = 4


class SchemaParseError(Exception):
    """DDL のテーブル定義またはカラム定義を解析できなかったことを表す例外。"""


class TableAttributionError(Exception):
    """テーブルを所有サービス 1 つに対応付けられなかったことを表す例外。"""


class EmptyBaselineError(Exception):
    """比較元にテーブルが 1 つも無く、削除を検出できないことを表す例外。"""


class EmptyCandidateError(Exception):
    """これから適用する union にテーブルが 1 つも無いことを表す例外。"""


def _quoted_region_end(sql: str, start: int) -> int | None:
    """指定位置から始まる引用領域の終端の次の位置を返します。

    Args:
        sql: 走査対象の SQL。
        start: 判定する位置。

    Returns:
        文字列リテラル・引用識別子・ドル引用のいずれかが始まるとき、それを閉じた
        直後の位置。引用が始まらないときは None。

    Raises:
        SchemaParseError: 引用が閉じられないまま SQL が終わる場合。
    """
    quote = sql[start]
    if quote in ("'", '"'):
        i = start + 1
        while i < len(sql):
            if sql[i] != quote:
                i += 1
            elif sql[i + 1:i + 2] == quote:
                i += 2
            else:
                return i + 1
        raise SchemaParseError(
            f"unterminated {quote} quoted text starting at {sql[start:start + 40]!r}"
        )
    if quote != "$":
        return None
    match = DOLLAR_QUOTE_RE.match(sql, start)
    if match is None:
        return None
    end = sql.find(match.group(), match.end())
    if end == -1:
        raise SchemaParseError(
            f"unterminated {match.group()} quoted text starting at {sql[start:start + 40]!r}"
        )
    return end + len(match.group())


def _block_comment_end(sql: str, start: int) -> int:
    """ブロックコメントの終端の次の位置を返します。

    Args:
        sql: 走査対象の SQL。
        start: `/*` の位置。

    Returns:
        入れ子を含めてコメントを閉じた直後の位置。

    Raises:
        SchemaParseError: コメントが閉じられないまま SQL が終わる場合。
    """
    depth = 1
    i = start + 2
    while i < len(sql):
        if sql.startswith("/*", i):
            depth += 1
            i += 2
        elif sql.startswith("*/", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    raise SchemaParseError(
        f"unterminated block comment starting at {sql[start:start + 40]!r}"
    )


def _strip_comments(sql: str) -> str:
    """SQL からコメントを取り除きます。

    Args:
        sql: 対象の SQL。

    Returns:
        行コメントとブロックコメントを空白 1 文字に置き換えた SQL。引用領域の内側は
        コメントとみなさない。

    Raises:
        SchemaParseError: 引用またはブロックコメントが閉じられない場合。
    """
    kept: list[str] = []
    i = 0
    while i < len(sql):
        quoted_end = _quoted_region_end(sql, i)
        if quoted_end is not None:
            kept.append(sql[i:quoted_end])
            i = quoted_end
        elif sql.startswith("--", i):
            newline = sql.find("\n", i)
            kept.append(" ")
            i = len(sql) if newline == -1 else newline
        elif sql.startswith("/*", i):
            kept.append(" ")
            i = _block_comment_end(sql, i)
        else:
            kept.append(sql[i])
            i += 1
    return "".join(kept)


def _matching_paren(sql: str, start: int) -> int:
    """開き括弧に対応する閉じ括弧の位置を返します。

    Args:
        sql: 走査対象の SQL。
        start: 開き括弧の次の位置。

    Returns:
        対応する閉じ括弧の位置。

    Raises:
        SchemaParseError: 括弧が閉じられないまま SQL が終わる場合。
    """
    depth = 1
    i = start
    while i < len(sql):
        quoted_end = _quoted_region_end(sql, i)
        if quoted_end is not None:
            i = quoted_end
            continue
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise SchemaParseError(
        f"unbalanced parentheses in the column list starting at {sql[start:start + 40]!r}. "
        "A definition that does not close would drop its columns from the "
        "destructive-change check."
    )


def _find_table_definitions(sql: str) -> list[tuple[str, str]]:
    """カラム定義を伴う CREATE TABLE を取り出します。

    Args:
        sql: コメントを除去済みの SQL。

    Returns:
        (テーブル名, 括弧内の本体テキスト) を出現順に並べたもの。引用領域の内側は
        走査しない。

    Raises:
        SchemaParseError: 引用または括弧が閉じられない場合。
    """
    definitions: list[tuple[str, str]] = []
    i = 0
    while i < len(sql):
        quoted_end = _quoted_region_end(sql, i)
        if quoted_end is not None:
            i = quoted_end
            continue
        match = TABLE_HEADER_RE.match(sql, i)
        if match is None:
            i += 1
            continue
        body_end = _matching_paren(sql, match.end())
        definitions.append((match.group(1), sql[match.end():body_end]))
        i = body_end + 1
    return definitions


def _count_create_table_statements(sql: str) -> int:
    """テーブルを宣言する文の出現数を数えます。

    Args:
        sql: コメントを除去済みの SQL。

    Returns:
        引用領域の外に現れる CREATE TABLE の個数。UNLOGGED・一時・外部テーブルの
        指定を伴う形も 1 件として数える。

    Raises:
        SchemaParseError: 引用が閉じられない場合。
    """
    count = 0
    i = 0
    while i < len(sql):
        quoted_end = _quoted_region_end(sql, i)
        if quoted_end is not None:
            i = quoted_end
            continue
        match = CREATE_TABLE_KEYWORD_RE.match(sql, i)
        if match is None:
            i += 1
            continue
        count += 1
        i = match.end()
    return count


def _split_top_level(body: str) -> list[str]:
    """CREATE TABLE 本体を最上位のカンマで分割します。

    Args:
        body: CREATE TABLE の括弧内の本体テキスト。

    Returns:
        カラム定義と表制約の並び。括弧の内側と引用領域の内側のカンマでは分割しない。

    Raises:
        SchemaParseError: 引用が閉じられない場合。
    """
    items: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(body):
        quoted_end = _quoted_region_end(body, i)
        if quoted_end is not None:
            i = quoted_end
            continue
        if body[i] == "(":
            depth += 1
        elif body[i] == ")":
            depth -= 1
        elif body[i] == "," and depth == 0:
            items.append(body[start:i])
            start = i + 1
        i += 1
    items.append(body[start:])
    return items


def _is_table_constraint(tokens: list[str]) -> bool:
    """CREATE TABLE 本体の要素が表制約かどうかを判定します。

    Args:
        tokens: 要素を空白で区切ったトークン列。

    Returns:
        表制約なら True、カラム定義の可能性が残るなら False。
    """
    head = tokens[0].upper()
    if head in CONSTRAINT_KEYWORDS:
        return True
    if head != EXCLUDE_CONSTRAINT_KEYWORD:
        return False
    # EXCLUDE は予約語ではなくカラム名にも使えるため、除外制約の構文どおり索引方式
    # または除外要素の括弧が続くときだけ制約とみなす
    following = tokens[1] if len(tokens) > 1 else ""
    return following.upper() == EXCLUDE_INDEX_METHOD_KEYWORD or following.startswith("(")


def _extract_columns(body: str, table_name: str) -> set[str]:
    """CREATE TABLE 本体からカラム名を抽出します。

    Args:
        body: CREATE TABLE の括弧内の本体テキスト。
        table_name: エラーメッセージに添えるテーブル名。

    Returns:
        正規化済みのカラム名集合。

    Raises:
        SchemaParseError: カラム名にも表制約にも分類できない要素がある場合、または
            カラムを 1 つも抽出できなかった場合。
    """
    columns: set[str] = set()
    for item in _split_top_level(body):
        tokens = item.split()
        if not tokens:
            continue
        if _is_table_constraint(tokens):
            continue
        first_token = tokens[0]
        if not COLUMN_IDENTIFIER_RE.match(first_token):
            raise SchemaParseError(
                f"table {table_name!r} has an element starting with {first_token!r}, "
                "which is neither a column name nor a table constraint. Columns hidden "
                "behind an unrecognized element would be silently excluded from the "
                "destructive-change check."
            )
        # PostgreSQL は引用符なし識別子を小文字へ畳み、引用符付きは大小を保持する。
        columns.add(first_token.strip('"') if first_token.startswith('"') else first_token.lower())

    # 全カラムを取りこぼしたテーブルは、カラムを持たないテーブルと区別が付かない。
    # そのまま通すと全カラムの削除が破壊的変更として警告されないため中断する
    if not columns:
        raise SchemaParseError(
            f"table {table_name!r} yielded no columns. Dropping every column of a "
            "table parsed as column-less would go unreported."
        )
    return columns


def parse_schema(union_sql: str) -> dict[str, set[str]]:
    """union SQL からテーブル定義をパースし、テーブル名→カラム名集合のマップを返します。

    Args:
        union_sql: fetch-schemas.py が生成した union SQL。

    Returns:
        `<所有サービス>.<テーブル名>` をキーとし、カラム名集合を値とするマップ。

    Raises:
        SchemaParseError: SQL 中の CREATE TABLE に解析できないものがある場合、または
            カラムを読み取れない CREATE TABLE がある場合。
        TableAttributionError: 所有サービスを特定できない、または同じ所有サービスで
            テーブル名が重複する場合。
    """
    tables: dict[str, set[str]] = {}
    parsed_count = 0
    declared_count = 0
    for owner, section in split_by_source(union_sql):
        # 行末コメントはカラム定義の区切りを跨ぐため、解析の前に取り除く。サービス区分の
        # 見出しもコメントなので、除去は split_by_source より後でなければならない
        statements = _strip_comments(section)
        declared_count += _count_create_table_statements(statements)
        for name, body in _find_table_definitions(statements):
            parsed_count += 1
            qualified = name.lower()
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
            tables[key] = _extract_columns(body, key)

    # 解析できない CREATE TABLE を 0 件と同一視すると、そのテーブルの削除が
    # 破壊的変更として警告されないまま適用されるため、件数の食い違いで中断する
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
        SchemaParseError: 解析できない CREATE TABLE、またはカラムを読み取れない
            CREATE TABLE がある場合。どのファイルかを併記する。
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


def check(baseline_path: str, candidate_path: str) -> list[str]:
    """比較元とこれから適用するスキーマを比較し、破壊的変更の警告リストを返します。

    Raises:
        EmptyBaselineError: 比較元にテーブルが 1 つも無い場合。
        SchemaParseError: いずれかのスキーマに解析できない CREATE TABLE、または
            カラムを読み取れない CREATE TABLE がある場合。
        TableAttributionError: いずれかのスキーマでテーブルを所有サービスに
            対応付けられない場合。
    """
    baseline_schema = _parse_schema_file(baseline_path)
    # テーブルを 1 つも持たない比較元は、いくつテーブルを消しても差分が出ないため、
    # 破壊的変更が無いことの根拠にならない
    if not baseline_schema:
        raise EmptyBaselineError(
            f"{baseline_path}: the baseline holds no table. Every table the new schema "
            "drops would go unreported."
        )
    candidate_schema = _parse_schema_file(candidate_path)

    warnings: list[str] = []

    dropped_tables = set(baseline_schema) - set(candidate_schema)
    for t in sorted(dropped_tables):
        warnings.append(f"DROP TABLE: {t}")

    for table in sorted(set(baseline_schema) & set(candidate_schema)):
        dropped_cols = baseline_schema[table] - candidate_schema[table]
        for col in sorted(dropped_cols):
            warnings.append(f"DROP COLUMN: {table}.{col}")

    return warnings


def verify_recordable_union(candidate_path: str) -> dict[str, set[str]]:
    """これから適用する union が、次回以降の比較元になれることを確かめます。

    Returns:
        `<所有サービス>.<テーブル名>` をキーとし、カラム名集合を値とするマップ。

    Raises:
        EmptyCandidateError: テーブルが 1 つも無い場合。
        SchemaParseError: 解析できない CREATE TABLE、またはカラムを読み取れない
            CREATE TABLE がある場合。
        TableAttributionError: テーブルを所有サービスに対応付けられない場合。
    """
    candidate_schema = _parse_schema_file(candidate_path)
    if not candidate_schema:
        raise EmptyCandidateError(
            f"{candidate_path}: the schema union to apply holds no table. Recording it "
            "as the first baseline would leave later runs with nothing to compare."
        )
    return candidate_schema


def _parse_args() -> argparse.Namespace:
    """コマンドライン引数を解析します。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="環境に適用済みの union。未記録なら存在しなくてよい")
    parser.add_argument("candidate", help="これから適用する union")
    parser.add_argument(
        "--bootstrap-baseline",
        action="store_true",
        help="適用済み union が未記録の環境で、比較せずに初回適用することを許可する",
    )
    return parser.parse_args()


def _exit_on_baseline_availability(has_baseline: bool, bootstrap_baseline: bool) -> None:
    """比較元の有無と初回適用の申告が食い違うとき、検査へ進まず終了します。"""
    if not has_baseline and not bootstrap_baseline:
        print("⚠ Schema safety check: no schema union has been recorded as applied yet.")
        print()
        print("Nothing can be compared, so destructive changes would go undetected.")
        print("If this is the first apply for this environment, re-run the manual workflow")
        print("with bootstrap_baseline=true to apply and record the first baseline.")
        sys.exit(EXIT_NO_BASELINE)

    if has_baseline and bootstrap_baseline:
        print("⚠ Schema safety check: a schema union is already recorded for this environment.")
        print()
        print("bootstrap_baseline only covers the first apply. Re-run without it so that")
        print("destructive changes are checked against the recorded union.")
        sys.exit(EXIT_USAGE)


def main() -> None:
    """スキーマ安全性チェックのメインエントリーポイントです。"""
    args = _parse_args()
    has_baseline = os.path.exists(args.baseline)
    _exit_on_baseline_availability(has_baseline, args.bootstrap_baseline)

    try:
        if not has_baseline:
            # 初回に適用する union はそのまま次回の比較元になるため、比較しない実行でも
            # 比較元として使える形かどうかは確かめる
            first_baseline = verify_recordable_union(args.candidate)
            print("Schema safety check: NOT PERFORMED (first apply for this environment)")
            print(f"The union to apply holds {len(first_baseline)} table(s) and becomes the "
                  "baseline for the next run.")
            sys.exit(EXIT_SAFE)
        warnings = check(args.baseline, args.candidate)
    except EmptyBaselineError as e:
        print(f"⚠ Schema safety check: the recorded schema union is unusable: {e}")
        print()
        print("A recorded union always holds the tables that were applied, so an empty one")
        print("means the record is broken. Investigate it before applying.")
        print("Recovering from a broken record is described in db-migrate/README.md.")
        sys.exit(EXIT_UNCHECKABLE)
    except EmptyCandidateError as e:
        print(f"⚠ Schema safety check: the schema union to apply is unusable: {e}")
        print()
        print("A union built from the service repositories always holds their tables, so an")
        print("empty one means the build went wrong. Investigate it before applying.")
        sys.exit(EXIT_UNCHECKABLE)
    except SchemaParseError as e:
        print(f"⚠ Schema safety check: cannot parse schema: {e}")
        print()
        print("The destructive-change check cannot cover tables and columns it failed to parse.")
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
