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
  description = "Container image for drift-monitor"
  type        = string
}

variable "github_token_secret" {
  description = "Secret Manager secret name for GITHUB_TOKEN"
  type        = string
  default     = "github-pat-nightly-review"
}

variable "tf_state_bucket" {
  description = "GCS bucket for Terraform state"
  type        = string
  default     = "keyandnotes-tf-state"
}

variable "targets" {
  description = "監視対象リポジトリ・環境の定義。Python スクリプトへ JSON で渡され、project は IAM 付与にも使用"
  type = list(object({
    repo = string
    environments = list(object({
      name    = string
      path    = string
      project = string
    }))
  }))
}
