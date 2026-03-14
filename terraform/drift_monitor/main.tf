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

locals {
  monitored_projects = distinct(flatten([
    for t in var.targets : [for e in t.environments : e.project]
  ]))
}

# --- Secrets (枠のみ。バージョンは手動登録) ---

resource "google_secret_manager_secret" "slack_webhook" {
  secret_id = var.slack_webhook_secret

  replication {
    auto {}
  }
}

# --- Service Account ---

resource "google_service_account" "drift_monitor" {
  account_id   = "drift-monitor"
  display_name = "Terraform Drift Monitor Job SA"
}

resource "google_secret_manager_secret_iam_member" "slack_webhook" {
  secret_id = google_secret_manager_secret.slack_webhook.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.drift_monitor.email}"
}

# Terraform state bucket への読み取り
resource "google_storage_bucket_iam_member" "tf_state_reader" {
  bucket = var.tf_state_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.drift_monitor.email}"
}

# 各プロジェクトの viewer（terraform plan に必要）
resource "google_project_iam_member" "viewer" {
  for_each = toset(local.monitored_projects)
  project  = each.value
  role     = "roles/viewer"
  member   = "serviceAccount:${google_service_account.drift_monitor.email}"
}

# --- Cloud Run Job ---

resource "google_cloud_run_v2_job" "drift_monitor" {
  name     = "drift-monitor"
  location = var.region

  template {
    task_count = 1

    template {
      service_account = google_service_account.drift_monitor.email
      timeout         = "1800s"

      containers {
        image = var.image

        env {
          name = "SLACK_WEBHOOK_URL"
          value_source {
            secret_key_ref {
              secret  = var.slack_webhook_secret
              version = "latest"
            }
          }
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
          name  = "TARGETS_JSON"
          value = jsonencode(var.targets)
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }
      }

      max_retries = 0
    }
  }
}

# --- Cloud Scheduler ---

resource "google_cloud_scheduler_job" "drift_monitor" {
  name      = "drift-monitor"
  region    = var.region
  schedule  = "0 7 * * *"
  time_zone = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.drift_monitor.name}:run"

    oauth_token {
      service_account_email = google_service_account.drift_monitor.email
    }
  }
}

resource "google_cloud_run_v2_job_iam_member" "invoker" {
  name     = google_cloud_run_v2_job.drift_monitor.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.drift_monitor.email}"
}
