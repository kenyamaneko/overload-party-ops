variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run Jobs and Cloud Scheduler"
  type        = string
  default     = "asia-northeast1"
}

variable "image" {
  description = "Container image for the nightly review job (e.g. asia-northeast1-docker.pkg.dev/PROJECT/REPO/nightly-review:latest)"
  type        = string
}

variable "anthropic_api_key_secret" {
  description = "Secret Manager secret name for ANTHROPIC_API_KEY"
  type        = string
  default     = "anthropic-api-key"
}

variable "github_token_secret" {
  description = "Secret Manager secret name for GITHUB_TOKEN"
  type        = string
  default     = "github-pat-nightly-review"
}

variable "repos" {
  description = "レビュー対象リポジトリ一覧。Python スクリプトへ JSON で渡す"
  type        = list(string)
  default = [
    "overload-party-common",
    "overload-party-client",
    "overload-party-battle",
    "overload-party-gateway",
    "overload-party-infra",
    "overload-party-k8s",
    "overload-party-newsfeed",
    "overload-party-analytics",
    "overload-party-ops",
  ]
}
