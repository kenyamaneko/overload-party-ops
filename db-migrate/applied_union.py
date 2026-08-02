#!/usr/bin/env python3
"""環境に適用済みの schema union をコンテナイメージとして記録し、次回の比較元として取り出します。

引数:
  fetch  --image-base BASE --environment ENV --out PATH
  record --image-base BASE --environment ENV [--dockerfile PATH] [--context DIR]
"""
import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

APPLIED_TAG = "latest"
UNION_PATH_IN_IMAGE = "/schema_union.sql"

# scratch イメージは実行できる命令を持たず docker create が命令の指定を求めるため、
# 取り出しのためだけに作るコンテナへ形ばかりの命令を渡す
EXTRACT_PLACEHOLDER_COMMAND = UNION_PATH_IN_IMAGE

# レジストリが「その参照は無い」と答えたことを示す語。認証失敗 (denied / unauthorized) や
# 通信断 (no such host / connection refused) と取り違えないために、存在しないと答えたときの
# 文言だけを並べる。docker のバージョンで文言が変わるため複数を持つ。
ABSENT_IMAGE_MARKERS = (
    "manifest unknown",
    "manifest_unknown",
    "name unknown",
    "name_unknown",
    "not found",
)


@dataclass(frozen=True)
class CommandResult:
    """外部コマンドの実行結果。"""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], CommandResult]


class ImageCommandError(Exception):
    """適用済み union を扱うコンテナ操作が失敗したことを表す例外。"""


def run_command(command: list[str]) -> CommandResult:
    """外部コマンドを実行して結果を返します。"""
    completed = subprocess.run(command, capture_output=True, text=True)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def applied_image_ref(image_base: str, environment: str) -> str:
    """環境の適用済み union を保持するイメージの参照を組み立てます。"""
    # Artifact Registry のクリーンアップは新しい方から一定数をパッケージ単位で残すため、
    # 環境ごとにパッケージを分けないと実行頻度の低い環境の記録が先に消える
    return f"{image_base}-applied-{environment}:{APPLIED_TAG}"


def _describe(command: list[str], result: CommandResult) -> str:
    """失敗したコマンドと、その出力をまとめた文言を組み立てます。"""
    output = (result.stderr + result.stdout).strip()
    return f"{' '.join(command)} failed (exit {result.returncode}): {output}"


def _run_checked(run: Runner, command: list[str]) -> str:
    """コマンドを実行し、失敗したら中断します。

    Returns:
        標準出力。前後の空白は取り除く。

    Raises:
        ImageCommandError: コマンドが非ゼロで終了した場合。
    """
    result = run(command)
    if result.returncode != 0:
        raise ImageCommandError(_describe(command, result))
    return result.stdout.strip()


def _is_absent(result: CommandResult) -> bool:
    """取得の失敗が、まだ記録が無いことによるものかを判定します。"""
    output = (result.stderr + result.stdout).lower()
    return any(marker in output for marker in ABSENT_IMAGE_MARKERS)


def fetch_applied_union(
    image_base: str,
    environment: str,
    out_path: str,
    *,
    run: Runner = run_command,
) -> bool:
    """環境に適用済みの union を取り出します。

    Args:
        out_path: 取り出した union の書き出し先。まだ記録が無いときは、前回の残りを
            比較元と取り違えないよう既存のファイルを消したうえで作成しない。

    Returns:
        記録があり取り出せたとき True、まだ記録が無いとき False。

    Raises:
        ImageCommandError: 記録の有無を判別できないまま取得に失敗した場合、または
            取り出しに失敗した場合。
    """
    Path(out_path).unlink(missing_ok=True)
    ref = applied_image_ref(image_base, environment)

    pull = ["docker", "pull", ref]
    result = run(pull)
    if result.returncode != 0:
        if _is_absent(result):
            return False
        raise ImageCommandError(_describe(pull, result))

    container = _run_checked(run, ["docker", "create", ref, EXTRACT_PLACEHOLDER_COMMAND])
    _run_checked(run, ["docker", "cp", f"{container}:{UNION_PATH_IN_IMAGE}", out_path])
    _run_checked(run, ["docker", "rm", "--force", container])
    return True


def record_applied_union(
    image_base: str,
    environment: str,
    dockerfile: str,
    context_dir: str,
    *,
    run: Runner = run_command,
) -> None:
    """適用した union を環境の記録として保存します。

    Args:
        context_dir: `schema_union.sql` を含むビルドコンテキスト。

    Raises:
        ImageCommandError: イメージのビルドまたは push が失敗した場合。
    """
    ref = applied_image_ref(image_base, environment)
    _run_checked(run, ["docker", "build", "-f", dockerfile, "-t", ref, context_dir])
    _run_checked(run, ["docker", "push", ref])


def _run_fetch(args: argparse.Namespace) -> None:
    """取り出しの副コマンドを実行します。"""
    if fetch_applied_union(args.image_base, args.environment, args.out):
        print(f"==> fetched the schema union applied to {args.environment}")
    else:
        print(f"==> no schema union recorded for {args.environment} yet")


def _run_record(args: argparse.Namespace) -> None:
    """記録の副コマンドを実行します。"""
    record_applied_union(args.image_base, args.environment, args.dockerfile, args.context)
    print(f"==> recorded the schema union applied to {args.environment}")


def _build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数のパーサを組み立てます。"""
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(required=True)

    fetch = subcommands.add_parser("fetch", help="環境に適用済みの union を取り出す")
    fetch.add_argument("--image-base", required=True)
    fetch.add_argument("--environment", required=True)
    fetch.add_argument("--out", required=True)
    fetch.set_defaults(handler=_run_fetch)

    record = subcommands.add_parser("record", help="適用した union を環境の記録として保存する")
    record.add_argument("--image-base", required=True)
    record.add_argument("--environment", required=True)
    record.add_argument("--dockerfile", default="db-migrate/applied-union.Dockerfile")
    record.add_argument("--context", default="db-migrate/sql")
    record.set_defaults(handler=_run_record)
    return parser


def main() -> int:
    """適用済み union の記録・取得のメインエントリーポイントです。"""
    args = _build_parser().parse_args()
    try:
        args.handler(args)
    except ImageCommandError as e:
        raise SystemExit(f"ERROR: {e}") from e
    return 0


if __name__ == "__main__":
    sys.exit(main())
