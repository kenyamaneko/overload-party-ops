#!/usr/bin/env python3
"""schemas.lock.yaml に記載されたサービスごとの DDL を取得し、union SQL を構築します。

CI (GitHub Actions runner) 上で docker build 前に実行される。各サービスリポジトリを
sparse-checkout でクローンし、lock ファイルに固定された ref のスキーマファイルのみ取得する。
結合結果は db-migrate/sql/schema_union.sql に書き出され、psqldef が適用する。

fetch を Docker 外で行う理由:
  - Git 認証は workflow (DB_MIGRATE_TOKEN secret) にあり、イメージに含めない
  - sql/ の COPY レイヤーハッシュでキャッシュが効くため PAT を中間レイヤーに含めない
  - 旧スキーマとの差分チェックもイメージ外の方が容易

引数:
  --lock PATH          schemas.lock.yaml パス (デフォルト: db-migrate/schemas.lock.yaml)
  --out  PATH          union 出力ファイル (デフォルト: db-migrate/sql/schema_union.sql)
  --seed-out PATH      seed union 出力ファイル。指定したときだけ seed を取得する
  --workdir DIR        クローン用作業ディレクトリ (デフォルト: /tmp/schema-src)
  --token TOKEN        GitHub PAT (環境変数 GITHUB_TOKEN / DB_MIGRATE_TOKEN fallback)
  --ref-override REF   全エントリを強制的にこの ref に上書き (CI dispatch override)
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# schemas.lock.yaml から取得したスキーマ名がファイルパスや SQL に到達する前に
# バリデーションするための PostgreSQL unquoted identifier ルール。
# 不正な値（例: `../etc/passwd` や `foo; DROP SCHEMA`）を即座に弾く。
_SCHEMA_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

_BANNER = "-- " + "=" * 77


def _validate_schema_name(schema_name: str, *, source: str) -> None:
    """スキーマ名が PostgreSQL unquoted identifier ルールに適合するか検証します。"""
    if not isinstance(schema_name, str) or not _SCHEMA_NAME_RE.match(schema_name):
        raise SystemExit(
            f"ERROR: invalid schema name {schema_name!r} ({source}). "
            "Must match PostgreSQL unquoted identifier rules: ^[a-z_][a-z0-9_]*$"
        )


def _load_yaml(path: Path) -> dict:
    """YAML ファイルを読み込みます。"""
    import yaml  # type: ignore
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _github_auth_env(token: str | None) -> dict[str, str]:
    """github.com への認証を git に渡す環境変数を組み立てます。

    Args:
        token: GitHub PAT。None のときは認証を付けず公開リポジトリとして扱う。

    Returns:
        git の実行に渡す環境変数。

    Raises:
        SystemExit: 呼び出し元の環境が既に GIT_CONFIG_COUNT を設定している場合。
    """
    env = os.environ.copy()
    if not token:
        return env

    if "GIT_CONFIG_COUNT" in env:
        raise SystemExit(
            "ERROR: GIT_CONFIG_COUNT is already set in the environment. "
            "Overwriting it would silently drop the inherited git config entries."
        )

    # subprocess の例外はコマンド引数をそのままメッセージに含めるため、
    # 認証情報を URL ではなく環境変数経由の git 設定として渡す
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = f"url.https://x-access-token:{token}@github.com/.insteadOf"
    env["GIT_CONFIG_VALUE_0"] = "https://github.com/"
    return env


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    """外部コマンドを実行します。

    Args:
        cmd: 実行するコマンドと引数。
        cwd: コマンドを実行する作業ディレクトリ。
        env: コマンドに渡す環境変数。None のとき現在の環境を引き継ぐ。

    Raises:
        subprocess.CalledProcessError: コマンドが非ゼロで終了した場合。
    """
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _clone_sparse(repo: str, ref: str, file_paths: list[str], dest: Path, token: str | None) -> list[Path]:
    """GitHub リポジトリから指定ファイル群を shallow sparse-clone します。"""
    dest.mkdir(parents=True, exist_ok=True)

    url = f"https://github.com/{repo}.git"
    env = _github_auth_env(token)

    try:
        _run(["git", "init", "-q"], cwd=dest, env=env)
        _run(["git", "remote", "add", "origin", url], cwd=dest, env=env)
        _run(["git", "config", "core.sparseCheckout", "true"], cwd=dest, env=env)
        sparse_file = dest / ".git" / "info" / "sparse-checkout"
        sparse_file.parent.mkdir(parents=True, exist_ok=True)
        sparse_file.write_text("".join(p + "\n" for p in file_paths), encoding="utf-8")
        _run(["git", "fetch", "--depth=1", "origin", ref], cwd=dest, env=env)
        _run(["git", "checkout", "FETCH_HEAD"], cwd=dest, env=env)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"ERROR: git operation failed for schemas.lock.yaml entry "
            f"{repo}@{ref}:{', '.join(file_paths)} (exit {e.returncode}). "
            f"Check that the repo / ref exists and DB_MIGRATE_TOKEN has read access."
        ) from e

    targets = []
    for file_path in file_paths:
        target = dest / file_path
        if not target.is_file():
            raise SystemExit(
                f"ERROR: expected file {file_path} not found in {repo}@{ref}. "
                f"schemas.lock.yaml entry is stale."
            )
        targets.append(target)
    return targets


def fetch_sources(lock: dict, workdir: Path, token: str | None, ref_override: str | None,
                  with_seeds: bool) -> list[dict]:
    """全サービスの DDL (と要求されていれば seed) を取得します。

    Args:
        lock: schemas.lock.yaml を読み込んだ辞書。
        workdir: クローン用の作業ディレクトリ。
        token: GitHub PAT。無い場合は匿名アクセス。
        ref_override: 全エントリの ref を上書きする値。None なら各エントリの ref。
        with_seeds: seed ファイルも取得するか。

    Returns:
        エントリごとに name / repo / ref / path / schema / seeds を持つ辞書のリスト。
        seeds は (パス, SQL) の組を lock の列挙順で保持する。
    """
    if "schemas" not in lock or not isinstance(lock["schemas"], list):
        raise SystemExit("ERROR: schemas.lock.yaml must contain a `schemas:` list")

    sources: list[dict] = []
    for entry in lock["schemas"]:
        name = entry["name"]
        repo = entry["repo"]
        path = entry["path"]
        ref = ref_override or entry.get("ref", "main")
        seed_paths = list(entry.get("seeds", [])) if with_seeds else []

        # `name` はスクラッチディレクトリ名と union SQL のバナーに使われる。
        # PostgreSQL identifier でない値はロックファイル改竄として即座に中断する
        # （パストラバーサルやシェルメタ文字に対する多重防御）。
        _validate_schema_name(name, source=f"schemas.lock.yaml entry for repo={repo!r}")

        wanted = [path, *seed_paths]
        print(f"==> fetching {name}: {repo}@{ref}:{', '.join(wanted)}", flush=True)
        dest = workdir / name
        if dest.exists():
            _run(["rm", "-rf", str(dest)])
        fetched = _clone_sparse(repo, ref, wanted, dest, token)

        sources.append({
            "name": name,
            "repo": repo,
            "ref": ref,
            "path": path,
            "schema": fetched[0].read_text(encoding="utf-8"),
            "seeds": [(p, f.read_text(encoding="utf-8")) for p, f in zip(seed_paths, fetched[1:])],
        })

    return sources


def render_union(sources: list[dict]) -> str:
    """取得済みの DDL を lock の列挙順に結合します。

    Args:
        sources: fetch_sources が返したエントリのリスト。

    Returns:
        psqldef に渡す union SQL。
    """
    chunks = _union_header("schema_union.sql")
    for src in sources:
        chunks.append(_BANNER)
        chunks.append(f"-- [{src['name']}]  source: {src['repo']}@{src['ref']}  path: {src['path']}")
        chunks.append(_BANNER)
        chunks.append(src["schema"].rstrip() + "\n")
    return "\n".join(chunks) + "\n"


def render_seed_union(sources: list[dict]) -> str:
    """取得済みの seed を lock の列挙順に結合します。

    Args:
        sources: fetch_sources が返したエントリのリスト。

    Returns:
        psql に渡す seed union SQL。seed を持つエントリが無ければヘッダのみ。
    """
    chunks = _union_header("seed_union.sql")
    for src in sources:
        for path, sql in src["seeds"]:
            chunks.append(_BANNER)
            chunks.append(f"-- [{src['name']}]  source: {src['repo']}@{src['ref']}  path: {path}")
            chunks.append(_BANNER)
            chunks.append(sql.rstrip() + "\n")
    return "\n".join(chunks) + "\n"


def _union_header(filename: str) -> list[str]:
    """union SQL の先頭に置く生成物バナーを組み立てます。"""
    return [
        _BANNER,
        f"-- {filename} — generated by db-migrate/fetch-schemas.py",
        "-- DO NOT EDIT. Edit schemas.lock.yaml and re-run the migration workflow.",
        _BANNER,
        "",
    ]


def main() -> int:
    """スキーマ取得・結合のメインエントリーポイントです。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", default="db-migrate/schemas.lock.yaml")
    parser.add_argument("--out", default="db-migrate/sql/schema_union.sql")
    parser.add_argument("--grant-src", default="db-migrate/grant_iam.sql",
                        help="Canonical grant_iam.sql path (committed in ops repo). "
                             "Copied alongside the union so Dockerfile COPY sql/ picks up both.")
    parser.add_argument("--seed-out", default=None,
                        help="Seed union output file. Seeds are fetched only when this is given; "
                             "the safety-diff build of the previous union omits it.")
    parser.add_argument("--workdir", default="/tmp/schema-src")
    parser.add_argument("--token", default=None)
    parser.add_argument("--ref-override", default=None)
    args = parser.parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("DB_MIGRATE_TOKEN")
    lock = _load_yaml(Path(args.lock))

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    sources = fetch_sources(lock, workdir, token, args.ref_override, with_seeds=args.seed_out is not None)
    union = render_union(sources)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(union, encoding="utf-8")
    print(f"==> wrote {out} ({len(union)} bytes, {union.count(chr(10))} lines)")

    if args.seed_out is not None:
        seed_union = render_seed_union(sources)
        seed_out = Path(args.seed_out)
        seed_out.parent.mkdir(parents=True, exist_ok=True)
        seed_out.write_text(seed_union, encoding="utf-8")
        print(f"==> wrote {seed_out} ({len(seed_union)} bytes, {seed_union.count(chr(10))} lines)")

    # sql/ は gitignore されているビルド成果物ディレクトリ。Dockerfile の
    # `COPY sql/ /app/sql/` が union / grant_iam 両方を拾えるよう、コミット済みの
    # grant_iam.sql を同じディレクトリにステージする。
    grant_src = Path(args.grant_src)
    if not grant_src.is_file():
        raise SystemExit(f"ERROR: grant_iam.sql not found at {grant_src}")
    grant_dst = out.parent / "grant_iam.sql"
    grant_dst.write_bytes(grant_src.read_bytes())
    print(f"==> staged {grant_dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
