#!/usr/bin/env bash
set -euo pipefail

IMAGE="${REGISTRY}/${AR_PROJECT}/${AR_REPOSITORY}/${IMAGE_NAME}:${IMAGE_SHA}"
gcloud run jobs update db-migrate \
  --region "${REGION}" \
  --project "${PROJECT}" \
  --image "${IMAGE}" \
  --quiet
