#!/bin/bash
set -euo pipefail

ENV="${1:?Usage: $0 <dev|stg|prod>}"

# ─── Config ─────────────────────────────────────────
case "${ENV}" in
  dev)  PROJECT="overload-party-dev" ;;
  stg)  PROJECT="overload-party-stg" ;;
  prod) PROJECT="overload-party-prod" ;;
  *)    echo "Unsupported environment: ${ENV}"; exit 1 ;;
esac
INSTANCE="overload-party-db"

# ─── Stop Cloud SQL ─────────────────────────────────
echo "==> Stopping Cloud SQL instance in ${PROJECT}..."
gcloud sql instances patch "${INSTANCE}" \
  --activation-policy=NEVER \
  --project="${PROJECT}" \
  --quiet

echo "==> Cloud SQL stopped."
