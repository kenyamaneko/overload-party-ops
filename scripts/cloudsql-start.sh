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

# ─── Start Cloud SQL ────────────────────────────────
echo "==> Starting Cloud SQL instance in ${PROJECT}..."
gcloud sql instances patch "${INSTANCE}" \
  --activation-policy=ALWAYS \
  --project="${PROJECT}" \
  --quiet

echo "==> Waiting for Cloud SQL to become RUNNABLE..."
for i in $(seq 1 30); do
  STATE=$(gcloud sql instances describe "${INSTANCE}" \
    --project="${PROJECT}" --format='value(state)')
  if [ "$STATE" = "RUNNABLE" ]; then
    echo "==> Cloud SQL is running."
    exit 0
  fi
  echo "    State: ${STATE} (${i}/30)..."
  sleep 10
done

echo "==> ERROR: Cloud SQL did not become RUNNABLE within timeout."
exit 1
