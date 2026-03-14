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
  description = "Container image for cost-monitor"
  type        = string
}

variable "slack_webhook_secret" {
  description = "Secret Manager secret name for Slack webhook URL"
  type        = string
  default     = "slack-webhook-cost-alert"
}

variable "environments" {
  description = "監視対象: 環境名 → GCP project ID。Python スクリプトへ JSON で渡され、IAM 付与にも使用"
  type        = map(string)
}

variable "gke_project" {
  description = "GCP project ID for GKE cluster"
  type        = string
  default     = "keyandnotes-platform"
}
