terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# --- Secrets (枠のみ。バージョンは手動登録) ---

# Slack Signing Secret は Cloudflare Worker 側で使用。Cloud Run からは参照しない。
resource "google_secret_manager_secret" "slack_signing_secret" {
  secret_id = var.slack_signing_secret

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "dispatch_secret" {
  secret_id = var.dispatch_secret

  replication {
    auto {}
  }
}

# slack-commands 専用の GitHub PAT (nightly-review とは別値を使う)
resource "google_secret_manager_secret" "github_token" {
  secret_id = var.github_token_secret

  replication {
    auto {}
  }
}

# --- Service Account ---

resource "google_service_account" "slack_commands" {
  account_id   = "slack-commands"
  display_name = "Slack Commands Service SA"
}

resource "google_secret_manager_secret_iam_member" "dispatch_secret" {
  secret_id = google_secret_manager_secret.dispatch_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.slack_commands.email}"
}

resource "google_secret_manager_secret_iam_member" "github_token" {
  secret_id = google_secret_manager_secret.github_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.slack_commands.email}"
}

# slack-webhook-url は真の共有 Secret として shared/ で管理される。
# slack-commands SA の accessor は shared/terraform.tfvars の
# slack_webhook_url_accessors リストに追加することで付与される。

# Cloud SQL 操作に必要（dev/stg プロジェクト）
resource "google_project_iam_member" "cloudsql_admin" {
  for_each = toset(var.cloudsql_projects)
  project  = each.value
  role     = "roles/cloudsql.admin"
  member   = "serviceAccount:${google_service_account.slack_commands.email}"
}

# --- Cloud Run Service ---

resource "google_cloud_run_v2_service" "slack_commands" {
  name     = "slack-commands"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  template {
    service_account = google_service_account.slack_commands.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      env {
        name = "GITHUB_TOKEN"
        value_source {
          secret_key_ref {
            secret  = var.github_token_secret
            version = "latest"
          }
        }
      }
      env {
        name = "DISPATCH_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.dispatch_secret
            version = "latest"
          }
        }
      }
      env {
        name  = "REPOS_JSON"
        value = jsonencode(var.repos)
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      startup_probe {
        http_get {
          path = "/health"
        }
      }
    }
  }
}

# Cloudflare Worker からの未認証リクエストを許可（認証は DISPATCH_SECRET で行う）
resource "google_cloud_run_v2_service_iam_member" "allow_unauthenticated" {
  name     = google_cloud_run_v2_service.slack_commands.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
