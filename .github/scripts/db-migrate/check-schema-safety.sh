#!/usr/bin/env bash
#
# 対象環境に適用済みの union を比較元として取り出し、これから適用する union と比較して
# 破壊的変更を検出する。
#
# BOOTSTRAP_BASELINE=true は、その環境にまだ適用済み union が記録されていないことを
# 承知の上で、比較せずに初回適用することを許可する。取り出しの結果は申告で変わらず、
# 記録の有無を判別できない失敗はどちらの実行でも中断する。
#
# 必要な環境変数:
#   BOOTSTRAP_BASELINE - true / false
#   BASELINE_UNION     - 比較元の書き出し先 (未記録なら作られない)
#   CANDIDATE_UNION    - これから適用する union のパス
set -euo pipefail

: "${BOOTSTRAP_BASELINE:?BOOTSTRAP_BASELINE is required}"
: "${BASELINE_UNION:?BASELINE_UNION is required}"
: "${CANDIDATE_UNION:?CANDIDATE_UNION is required}"

case "$BOOTSTRAP_BASELINE" in
  true)  bootstrap_option="--bootstrap-baseline" ;;
  false) bootstrap_option="" ;;
  *)
    echo "check-schema-safety: BOOTSTRAP_BASELINE must be true or false (got '$BOOTSTRAP_BASELINE')" >&2
    exit 1
    ;;
esac

db-migrate/fetch-applied-union.sh

python3 db-migrate/schema_check.py \
  ${bootstrap_option:+"$bootstrap_option"} \
  "$BASELINE_UNION" \
  "$CANDIDATE_UNION"
