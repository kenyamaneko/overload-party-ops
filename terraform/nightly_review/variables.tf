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
  default     = "github-token"
}
