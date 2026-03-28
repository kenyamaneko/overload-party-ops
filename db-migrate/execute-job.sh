#!/usr/bin/env bash
set -euo pipefail

gcloud run jobs execute db-migrate \
  --region "${REGION}" \
  --project "${PROJECT}" \
  --wait \
  --quiet
