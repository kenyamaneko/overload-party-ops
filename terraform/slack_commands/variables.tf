variable "project_id" {
  description = "GCP project ID (ops project)"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "asia-northeast1"
}

variable "image" {
  description = "Container image for the Cloud Run Service"
  type        = string
}

variable "github_token_secret" {
  description = "Secret Manager secret name for GitHub token"
  type        = string
  default     = "github-token"
}

variable "slack_signing_secret" {
  description = "Secret Manager secret name for Slack Signing Secret"
  type        = string
  default     = "slack-signing-secret"
}

variable "cloudsql_projects" {
  description = "Cloud SQL 操作を許可する GCP プロジェクト ID のリスト"
  type        = list(string)
  default     = ["overload-party-dev", "overload-party-stg"]
}

variable "repos" {
  description = "List of GitHub repositories to query"
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
