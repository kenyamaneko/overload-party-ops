#!/usr/bin/env bash
#
# 適用に成功した schema union を、対象環境の記録として Artifact Registry へ保存する。
# 次回のマイグレーションはこれを破壊的変更チェックの比較元にする。
#
# 必要な環境変数:
#   REGISTRY / AR_PROJECT / AR_REPOSITORY / IMAGE_NAME - マイグレーションイメージの参照要素
#   ENV - 対象環境 (dev / stg)
set -euo pipefail

: "${ENV:?ENV is required}"

python3 db-migrate/applied_union.py record \
  --image-base "${REGISTRY}/${AR_PROJECT}/${AR_REPOSITORY}/${IMAGE_NAME}" \
  --environment "${ENV}"
