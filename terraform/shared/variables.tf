variable "project_id" {
  description = "Google Cloud project ID (ops project)"
  type        = string
}

variable "region" {
  description = "Google Cloud region"
  type        = string
  default     = "asia-northeast1"
}

variable "github_pat_nightly_review_accessors" {
  description = "github-pat-nightly-review Secret にアクセスする SA の member 文字列リスト"
  type        = list(string)
}

variable "github_pat_slack_commands_accessors" {
  description = "github-pat-slack-commands Secret にアクセスする SA の member 文字列リスト"
  type        = list(string)
}

variable "slack_webhook_url_accessors" {
  description = "slack-webhook-url Secret にアクセスする SA の member 文字列リスト"
  type        = list(string)
}

variable "deploy_service_account" {
  description = "GitHub Actions の WIF で使用する deploy SA のメールアドレス"
  type        = string
}

variable "gke_project" {
  description = "GKE クラスタが存在するプロジェクト ID"
  type        = string
  default     = "keyandnotes-platform"
}

variable "tf_state_bucket" {
  description = "Terraform state の GCS バケット名"
  type        = string
  default     = "keyandnotes-tf-state"
}

variable "cost_monitor_projects" {
  description = "cost-monitor の監視対象プロジェクト（cloudsql.viewer, compute.viewer を付与）"
  type        = list(string)
}

variable "drift_monitor_projects" {
  description = "drift-monitor の監視対象プロジェクト（viewer, securityReviewer を付与）"
  type        = list(string)
}
