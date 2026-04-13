#!/usr/bin/env bash
set -euo pipefail

gcloud sql instances patch "${CLOUDSQL_INSTANCE_NAME}" \
  --project="${PROJECT}" \
  --activation-policy=ALWAYS --quiet

echo "インスタンスが RUNNABLE になるまで待機..."
for i in $(seq 1 60); do
  STATE=$(gcloud sql instances describe "${CLOUDSQL_INSTANCE_NAME}" \
    --project="${PROJECT}" --format='value(state)')
  if [ "$STATE" = "RUNNABLE" ]; then
    echo "Instance is RUNNABLE (attempt ${i})"
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "Instance failed to become RUNNABLE (state: ${STATE})"
    exit 1
  fi
  sleep 5
done
