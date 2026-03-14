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
  monitored_projects = distinct(values(var.environments))
}

# gcloud CLI は呼び出し元プロジェクトでも sqladmin API が有効である必要がある
resource "google_project_service" "sqladmin" {
  service            = "sqladmin.googleapis.com"
  disable_on_destroy = false
}

# --- Secrets (枠のみ。バージョンは手動登録) ---

resource "google_secret_manager_secret" "slack_webhook" {
  secret_id = var.slack_webhook_secret

  replication {
    auto {}
  }
}

# --- Service Account ---

resource "google_service_account" "cost_monitor" {
  account_id   = "cost-monitor"
  display_name = "Cost Monitor Job SA"
}

resource "google_secret_manager_secret_iam_member" "slack_webhook" {
  secret_id = google_secret_manager_secret.slack_webhook.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cost_monitor.email}"
}

# Cloud SQL の状態確認に必要（各監視対象プロジェクト）
resource "google_project_iam_member" "cloudsql_viewer" {
  for_each = toset(local.monitored_projects)
  project  = each.value
  role     = "roles/cloudsql.viewer"
  member   = "serviceAccount:${google_service_account.cost_monitor.email}"
}

# Compute リソース（Static IP, PSC）の確認に必要
resource "google_project_iam_member" "compute_viewer" {
  for_each = toset(local.monitored_projects)
  project  = each.value
  role     = "roles/compute.viewer"
  member   = "serviceAccount:${google_service_account.cost_monitor.email}"
}

# GKE Deployment/Ingress の確認に必要
resource "google_project_iam_member" "gke_viewer" {
  project = var.gke_project
  role    = "roles/container.viewer"
  member  = "serviceAccount:${google_service_account.cost_monitor.email}"
}

# --- Cloud Run Job ---

resource "google_cloud_run_v2_job" "cost_monitor" {
  name     = "cost-monitor"
  location = var.region

  template {
    task_count = 1

    template {
      service_account = google_service_account.cost_monitor.email
      timeout         = "300s"

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
          name  = "ENVIRONMENTS_JSON"
          value = jsonencode(var.environments)
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }

      max_retries = 0
    }
  }
}

# --- Cloud Scheduler ---

resource "google_cloud_scheduler_job" "cost_monitor" {
  name      = "cost-monitor"
  region    = var.region
  schedule  = "0 8 * * *"
  time_zone = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.cost_monitor.name}:run"

    oauth_token {
      service_account_email = google_service_account.cost_monitor.email
    }
  }
}

resource "google_cloud_run_v2_job_iam_member" "invoker" {
  name     = google_cloud_run_v2_job.cost_monitor.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.cost_monitor.email}"
}
