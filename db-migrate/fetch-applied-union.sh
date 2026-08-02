#!/usr/bin/env bash
#
# 対象環境に適用済みの schema union を取り出し、破壊的変更チェックの比較元として書き出す。
# まだ記録が無い環境では、比較元を作らずに正常終了する。
#
# 必要な環境変数:
#   REGISTRY / AR_PROJECT / AR_REPOSITORY / IMAGE_NAME - マイグレーションイメージの参照要素
#   ENV                 - 対象環境 (dev / stg)
#   BASELINE_UNION      - 比較元の書き出し先
set -euo pipefail

: "${ENV:?ENV is required}"
: "${BASELINE_UNION:?BASELINE_UNION is required}"

python3 db-migrate/applied_union.py fetch \
  --image-base "${REGISTRY}/${AR_PROJECT}/${AR_REPOSITORY}/${IMAGE_NAME}" \
  --environment "${ENV}" \
  --out "${BASELINE_UNION}"
