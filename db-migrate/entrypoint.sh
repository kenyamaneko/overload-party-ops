#!/bin/bash
set -euo pipefail

# db-migrate entrypoint -- Cloud Run Job コンテナ内で実行される。
#
# psqldef 失敗時に grant_iam.sql が半端なスキーマに適用されないよう、
# 明示的にガードしている（set -e と二重防御）。

echo "==> Applying schema union (psqldef)..."
if ! psqldef \
  --config=/app/sqldef.yml \
  --host="${DATABASE_HOST}" \
  --port="${DATABASE_PORT}" \
  --user="${DATABASE_USER}" \
  --password="${DATABASE_PASSWORD}" \
  "${DATABASE_NAME}" \
  < /app/sql/schema_union.sql; then
  echo "ERROR: psqldef failed — aborting before grant_iam.sql to avoid applying grants to a partially-migrated schema." >&2
  exit 1
fi

echo "==> Applying IAM grants..."
if ! PGPASSWORD="${DATABASE_PASSWORD}" psql \
  -v ON_ERROR_STOP=1 \
  -h "${DATABASE_HOST}" \
  -p "${DATABASE_PORT}" \
  -U "${DATABASE_USER}" \
  -d "${DATABASE_NAME}" \
  -f /app/sql/grant_iam.sql; then
  echo "ERROR: grant_iam.sql application failed." >&2
  exit 1
fi

echo "==> Migration complete."
