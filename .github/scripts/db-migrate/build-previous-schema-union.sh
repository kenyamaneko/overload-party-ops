#!/usr/bin/env bash
#
# HEAD~1 時点の schemas.lock.yaml を取り出し、その時点の schema union を生成する。
# 生成結果は schema_check.py による safety diff (破壊的変更検出) の比較元に使う。
#
# ops repo に前コミットが存在しない (初回) 場合は、比較対象が無いため空ファイルを出力する。
# これは「前の状態が存在しない」という事実の表現であり、エラーを握りつぶしているわけではない。
#
# 必要な環境変数:
#   DB_MIGRATE_TOKEN - fetch-schemas.py が service repo の schema を取得するのに使用
set -euo pipefail

: "${DB_MIGRATE_TOKEN:?DB_MIGRATE_TOKEN is required}"

OLD_LOCK=/tmp/schemas.lock.old.yaml
OLD_UNION=/tmp/schema_union.old.sql

if ! git rev-parse HEAD~1 >/dev/null 2>&1; then
  : > "$OLD_UNION"
  echo "build-previous-schema-union: no HEAD~1 (initial commit), emitted empty union"
  exit 0
fi

git show HEAD~1:db-migrate/schemas.lock.yaml > "$OLD_LOCK"
python3 db-migrate/fetch-schemas.py \
  --lock "$OLD_LOCK" \
  --out "$OLD_UNION" \
  --grant-src db-migrate/grant_iam.sql \
  --workdir /tmp/schema-src-old
