#!/bin/bash
set -euo pipefail

echo "==> Applying schema migration (psqldef)..."
# --config は ADR-014 の 7 サービススキーマを target_schema で明示する。
# 指定がないと psqldef が public 以外のスキーマを認識できず、スキーマ分割後の
# テーブルを全て「新規作成」扱いするか、存在しないと見做して drop しかねない。
psqldef \
  --config=/app/sqldef.yml \
  --host="${DATABASE_HOST}" \
  --port="${DATABASE_PORT}" \
  --user="${DATABASE_USER}" \
  --password="${DATABASE_PASSWORD}" \
  "${DATABASE_NAME}" \
  < /app/sql/schema_postgres.sql

echo "==> Applying IAM grants..."
PGPASSWORD="${DATABASE_PASSWORD}" psql \
  -h "${DATABASE_HOST}" \
  -p "${DATABASE_PORT}" \
  -U "${DATABASE_USER}" \
  -d "${DATABASE_NAME}" \
  -f /app/sql/grant_iam.sql

echo "==> Migration complete."
