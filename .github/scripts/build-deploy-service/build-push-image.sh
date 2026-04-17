#!/usr/bin/env bash
#
# 指定 service_dir からコンテナイメージをビルドし、Artifact Registry に :${GIT_SHA} と :latest で push する。
#
# 必要な環境変数:
#   REGISTRY       - Artifact Registry ホスト (例: asia-northeast1-docker.pkg.dev)
#   AR_PROJECT     - Artifact Registry 所属 GCP プロジェクト
#   AR_REPOSITORY  - Artifact Registry リポジトリ名
#   IMAGE_NAME     - イメージ名
#   SERVICE_DIR    - docker build のビルドコンテキスト
#   GIT_SHA        - タグに使用する commit sha (= github.sha)
set -euo pipefail

: "${REGISTRY:?REGISTRY is required}"
: "${AR_PROJECT:?AR_PROJECT is required}"
: "${AR_REPOSITORY:?AR_REPOSITORY is required}"
: "${IMAGE_NAME:?IMAGE_NAME is required}"
: "${SERVICE_DIR:?SERVICE_DIR is required}"
: "${GIT_SHA:?GIT_SHA is required}"

IMAGE="${REGISTRY}/${AR_PROJECT}/${AR_REPOSITORY}/${IMAGE_NAME}"

docker build \
  -t "${IMAGE}:${GIT_SHA}" \
  -t "${IMAGE}:latest" \
  "${SERVICE_DIR}/"
docker push "${IMAGE}:${GIT_SHA}"
docker push "${IMAGE}:latest"
