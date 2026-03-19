variable "project_id" {
  description = "GCP project ID (ops project)"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "asia-northeast1"
}

variable "github_token_accessors" {
  description = "github-token Secret にアクセスする SA の member 文字列リスト"
  type        = list(string)
}

variable "slack_webhook_url_accessors" {
  description = "slack-webhook-url Secret にアクセスする SA の member 文字列リスト"
  type        = list(string)
}
