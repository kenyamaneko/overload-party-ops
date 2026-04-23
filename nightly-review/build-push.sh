#!/usr/bin/env bash
#
# Nightly Review コンテナをビルドして Artifact Registry に push する。
# db-migrate/build-push.sh と同じパターン。:${IMAGE_SHA} と :latest の 2 タグで push する。
#
# 必要な環境変数:
#   REGISTRY       - Artifact Registry ホスト (例: asia-northeast1-docker.pkg.dev)
#   AR_PROJECT     - Artifact Registry 所属 GCP プロジェクト
#   AR_REPOSITORY  - Artifact Registry リポジトリ名
#   IMAGE_NAME     - イメージ名 (= nightly-review)
#   IMAGE_SHA      - タグに使う commit sha (= github.sha)
set -euo pipefail

IMAGE="${REGISTRY}/${AR_PROJECT}/${AR_REPOSITORY}/${IMAGE_NAME}"
docker build \
  -t "${IMAGE}:${IMAGE_SHA}" \
  -t "${IMAGE}:latest" \
  nightly-review/
docker push "${IMAGE}:${IMAGE_SHA}"
docker push "${IMAGE}:latest"
