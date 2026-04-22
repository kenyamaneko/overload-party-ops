"""Firestore game_config シード投入スクリプト。

Firestore Native モードの `game_config` コレクションに、common リポで定義された
初期値 (`data/game_config_defaults.yaml`) を投入する。

データの SSoT は common リポの YAML、本スクリプトは「取得 → 投入」の runner に
徹する (db-migrate/fetch-schemas.py と同じ役割分担)。取得元とバージョンは
`seed-sources.lock.yaml` で pin する。

配置ルール: overload-party-common/docs/architecture/DATA_DESIGN.md
ADR: overload-party-common/docs/adr/017-game-config-firestore.md

使い方:
    # sibling checkout (dev workstation の典型) — local 参照で完結
    python3 seed_game_config.py \\
        --project <PROJECT_ID> \\
        --source ../../overload-party-common/data/game_config_defaults.yaml

    # lock file 記載の ref を GitHub から fetch (CI / 再現性確保)
    python3 seed_game_config.py --project <PROJECT_ID> --fetch

    # ローカルエミュレーター接続
    #   別シェルで: gcloud emulators firestore start --host-port=localhost:9041
    FIRESTORE_EMULATOR_HOST=localhost:9041 \\
        python3 seed_game_config.py \\
            --project overload-party-local \\
            --source ../../overload-party-common/data/game_config_defaults.yaml

冪等: 既存ドキュメントはデフォルトでスキップ。--overwrite で上書き可。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from google.cloud import firestore

COLLECTION = "game_config"
LOCK_SOURCE_KEY = "game_config"


def load_defaults(source_path: Path) -> dict[str, Any]:
    """common リポの game_config_defaults.yaml を読み取り、key → value の辞書を返す。"""
    with source_path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    if not isinstance(doc, dict) or "defaults" not in doc:
        raise SystemExit(
            f"ERROR: {source_path} must contain top-level `defaults:` mapping"
        )

    defaults = doc["defaults"]
    if not isinstance(defaults, dict) or not defaults:
        raise SystemExit(f"ERROR: {source_path} `defaults:` must be a non-empty mapping")

    result: dict[str, Any] = {}
    for key, entry in defaults.items():
        if not isinstance(entry, dict) or "value" not in entry:
            raise SystemExit(
                f"ERROR: {source_path} entry {key!r} must be a mapping containing `value:`"
            )
        result[key] = entry["value"]
    return result


def fetch_from_lock(lock_path: Path, workdir: Path, token: str | None) -> Path:
    """lock file に記載された ref で common リポの YAML を sparse-clone して取得する。"""
    with lock_path.open("r", encoding="utf-8") as f:
        lock = yaml.safe_load(f)

    if not isinstance(lock, dict) or "sources" not in lock:
        raise SystemExit(f"ERROR: {lock_path} must contain top-level `sources:` mapping")

    entry = lock["sources"].get(LOCK_SOURCE_KEY)
    if not entry:
        raise SystemExit(
            f"ERROR: {lock_path} `sources.{LOCK_SOURCE_KEY}` is missing"
        )

    repo = entry["repo"]
    path = entry["path"]
    ref = entry.get("ref", "main")

    url = (
        f"https://x-access-token:{token}@github.com/{repo}.git"
        if token
        else f"https://github.com/{repo}.git"
    )

    workdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=workdir, check=True)
    subprocess.run(
        ["git", "config", "core.sparseCheckout", "true"], cwd=workdir, check=True
    )
    sparse_file = workdir / ".git" / "info" / "sparse-checkout"
    sparse_file.parent.mkdir(parents=True, exist_ok=True)
    sparse_file.write_text(path + "\n", encoding="utf-8")
    subprocess.run(["git", "fetch", "--depth=1", "origin", ref], cwd=workdir, check=True)
    subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=workdir, check=True)

    target = workdir / path
    if not target.is_file():
        raise SystemExit(
            f"ERROR: expected file {path} not found in {repo}@{ref}. "
            f"seed-sources.lock.yaml entry is stale."
        )
    print(f"==> fetched {repo}@{ref}:{path}")
    return target


def seed(project: str, values: dict[str, Any], overwrite: bool) -> None:
    """Firestore `game_config` コレクションへ初期値を投入する。"""
    client = firestore.Client(project=project)
    col = client.collection(COLLECTION)
    for key, value in values.items():
        doc_ref = col.document(key)
        if not overwrite and doc_ref.get().exists:
            print(f"skip (exists): {key}")
            continue
        doc_ref.set({"value": value})
        print(f"set: {key} = {value}")


def resolve_source(args: argparse.Namespace) -> Path:
    """--source (local) もしくは --fetch (lock 経由) で YAML の実体パスを解決する。"""
    if args.source:
        path = Path(args.source)
        if not path.is_file():
            raise SystemExit(f"ERROR: --source file not found: {path}")
        return path

    if args.fetch:
        token = args.token
        workdir = Path(tempfile.mkdtemp(prefix="firestore-seed-"))
        return fetch_from_lock(Path(args.lock), workdir, token)

    raise SystemExit(
        "ERROR: must specify one of --source PATH or --fetch (see --help)"
    )


def main() -> int:
    """シードエントリーポイント。"""
    parser = argparse.ArgumentParser(description="Seed Firestore game_config")
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument(
        "--source",
        help="common の game_config_defaults.yaml へのローカルパス (dev 向け)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="seed-sources.lock.yaml の ref で common から YAML を sparse-clone する",
    )
    parser.add_argument(
        "--lock",
        default=str(Path(__file__).parent / "seed-sources.lock.yaml"),
        help="lock file パス (--fetch 時に参照)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub PAT (--fetch 時に private repo アクセス用)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存ドキュメントも上書きする (デフォルトはスキップ)",
    )
    args = parser.parse_args()

    source_path = resolve_source(args)
    values = load_defaults(source_path)
    seed(args.project, values, args.overwrite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
