#!/usr/bin/env bash
#
# Cloud Run サービスのイメージを :${GIT_SHA} に更新する。
#
# 必要な環境変数:
#   REGISTRY            - Artifact Registry ホスト
#   AR_PROJECT          - Artifact Registry 所属 GCP プロジェクト
#   AR_REPOSITORY       - Artifact Registry リポジトリ名
#   IMAGE_NAME          - イメージ名
#   GIT_SHA             - デプロイ対象の commit sha
#   CLOUD_RUN_SERVICE   - 更新対象の Cloud Run サービス名
#   REGION              - Cloud Run のリージョン
#   PROJECT             - Cloud Run サービス所属の GCP プロジェクト
set -euo pipefail

: "${REGISTRY:?REGISTRY is required}"
: "${AR_PROJECT:?AR_PROJECT is required}"
: "${AR_REPOSITORY:?AR_REPOSITORY is required}"
: "${IMAGE_NAME:?IMAGE_NAME is required}"
: "${GIT_SHA:?GIT_SHA is required}"
: "${CLOUD_RUN_SERVICE:?CLOUD_RUN_SERVICE is required}"
: "${REGION:?REGION is required}"
: "${PROJECT:?PROJECT is required}"

IMAGE="${REGISTRY}/${AR_PROJECT}/${AR_REPOSITORY}/${IMAGE_NAME}:${GIT_SHA}"

gcloud run services update "${CLOUD_RUN_SERVICE}" \
  --region "${REGION}" \
  --project "${PROJECT}" \
  --image "${IMAGE}" \
  --quiet
