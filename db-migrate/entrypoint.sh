#!/bin/bash
set -euo pipefail

echo "==> Applying schema migration (psqldef)..."
psqldef \
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
