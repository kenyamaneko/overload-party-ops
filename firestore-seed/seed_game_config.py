"""Firestore game_config シード投入スクリプト。

Firestore Native モードの `game_config` コレクションにゲーム設定の初期値を投入する。

使い方:
    # 本番/ステージング/dev の Google Cloud プロジェクト指定
    python3 seed_game_config.py --project <PROJECT_ID>

    # ローカルエミュレーター接続 (別シェルで以下を起動)
    #   gcloud emulators firestore start --host-port=localhost:9041
    FIRESTORE_EMULATOR_HOST=localhost:9041 \\
        python3 seed_game_config.py --project overload-party-local

冪等: set を使うので再実行しても安全。既存値を上書きする点に注意。
"""

from __future__ import annotations

import argparse
import sys

from google.cloud import firestore

COLLECTION = "game_config"

DEFAULT_VALUES: dict[str, int] = {
    "free_daily_battle_limit": 10,
    "premium_daily_battle_limit": 30,
    "initial_time_bank": 480,
    "exp_win": 40,
    "exp_loss": 20,
    "exp_draw": 30,
    "exp_formula_coefficient": 60,
}


def seed(project: str, overwrite: bool) -> None:
    client = firestore.Client(project=project)
    col = client.collection(COLLECTION)
    for key, value in DEFAULT_VALUES.items():
        doc_ref = col.document(key)
        if not overwrite and doc_ref.get().exists:
            print(f"skip (exists): {key}")
            continue
        doc_ref.set({"value": value})
        print(f"set: {key} = {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Firestore game_config")
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存ドキュメントも上書きする (デフォルトはスキップ)",
    )
    args = parser.parse_args()
    seed(args.project, args.overwrite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
