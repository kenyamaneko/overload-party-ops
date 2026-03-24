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

resource "google_secret_manager_secret" "slack_signing_secret" {
  secret_id = var.slack_signing_secret

  replication {
    auto {}
  }
}

# --- Service Account ---

resource "google_service_account" "slack_commands" {
  account_id   = "slack-commands"
  display_name = "Slack Commands Service SA"
}

resource "google_secret_manager_secret_iam_member" "slack_signing_secret" {
  secret_id = google_secret_manager_secret.slack_signing_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.slack_commands.email}"
}

# github-pat-nightly-review, github-pat-slack-commands, slack-webhook-url は shared/ で管理。accessors リストへの追加が必要。

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
        name = "SLACK_SIGNING_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.slack_signing_secret
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

# Slack からの未認証リクエストを許可（認証は Signing Secret で行う）
resource "google_cloud_run_v2_service_iam_member" "allow_unauthenticated" {
  name     = google_cloud_run_v2_service.slack_commands.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
