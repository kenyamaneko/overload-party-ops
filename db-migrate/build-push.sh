#!/usr/bin/env bash
set -euo pipefail

IMAGE="${REGISTRY}/${AR_PROJECT}/${AR_REPOSITORY}/${IMAGE_NAME}"
docker build \
  -t "${IMAGE}:${IMAGE_SHA}" \
  -t "${IMAGE}:latest" \
  db-migrate/
docker push "${IMAGE}:${IMAGE_SHA}"
docker push "${IMAGE}:latest"
