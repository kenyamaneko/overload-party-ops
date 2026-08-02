#!/usr/bin/env bash
#
# 適用済みの union とこれから適用する union を比較し、破壊的変更を検出する。
#
# BOOTSTRAP_BASELINE=true は、その環境にまだ適用済み union が記録されていないことを
# 承知の上で、比較せずに初回適用することを許可する。
#
# 必要な環境変数:
#   BOOTSTRAP_BASELINE - true / false
#   BASELINE_UNION     - 適用済み union のパス (未記録なら存在しない)
#   CANDIDATE_UNION    - これから適用する union のパス
set -euo pipefail

: "${BOOTSTRAP_BASELINE:?BOOTSTRAP_BASELINE is required}"
: "${BASELINE_UNION:?BASELINE_UNION is required}"
: "${CANDIDATE_UNION:?CANDIDATE_UNION is required}"

case "$BOOTSTRAP_BASELINE" in
  true)  allow_missing="--allow-missing-baseline" ;;
  false) allow_missing="" ;;
  *)
    echo "check-schema-safety: BOOTSTRAP_BASELINE must be true or false (got '$BOOTSTRAP_BASELINE')" >&2
    exit 1
    ;;
esac

python3 db-migrate/schema_check.py ${allow_missing:+"$allow_missing"} "$BASELINE_UNION" "$CANDIDATE_UNION"
