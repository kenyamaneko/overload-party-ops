# Cloudflare Worker — slack-commands-proxy.
#
# Worker のコードデプロイは引き続き wrangler (CI) で行う。
# Terraform はリソースの「枠」と設定のみ管理し、content の変更は無視する。
#
# 既存 Worker のインポート:
#   terraform import cloudflare_workers_script.slack_commands_proxy <ACCOUNT_ID>/slack-commands-proxy

terraform {
  required_version = ">= 1.5"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

variable "cloudflare_workers_api_token" {
  description = "Cloudflare API token (Workers Scripts edit)"
  type        = string
  sensitive   = true
}

provider "cloudflare" {
  api_token = var.cloudflare_workers_api_token
}

# ──────────────────────────────────────────────
# Variables
# ──────────────────────────────────────────────

variable "cloudflare_account_id" {
  description = "Cloudflare account ID"
  type        = string
}

variable "cloud_run_url" {
  description = "Cloud Run service URL for slack-commands"
  type        = string
}

# ──────────────────────────────────────────────
# Worker Script
# ──────────────────────────────────────────────

resource "cloudflare_workers_script" "slack_commands_proxy" {
  account_id = var.cloudflare_account_id
  name       = "slack-commands-proxy"
  module     = true

  # 初回作成用のプレースホルダ。実際のコードは wrangler deploy で管理する。
  content = "export default { async fetch() { return new Response('deployed by wrangler') } }"

  plain_text_binding {
    name = "CLOUD_RUN_URL"
    text = var.cloud_run_url
  }

  # コードは wrangler deploy で更新するため、Terraform では追跡しない
  lifecycle {
    ignore_changes = [content]
  }
}
