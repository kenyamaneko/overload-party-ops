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

# --- Service Account ---

resource "google_service_account" "nightly_review" {
  account_id   = "nightly-review"
  display_name = "Nightly Review Job SA"
}

resource "google_secret_manager_secret_iam_member" "anthropic_key" {
  secret_id = var.anthropic_api_key_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.nightly_review.email}"
}

resource "google_secret_manager_secret_iam_member" "github_token" {
  secret_id = var.github_token_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.nightly_review.email}"
}

# --- Cloud Run Jobs ---

resource "google_cloud_run_v2_job" "nightly_review_diff" {
  name     = "nightly-review-diff"
  location = var.region

  template {
    task_count = 1

    template {
      service_account = google_service_account.nightly_review.email
      timeout         = "3600s"

      containers {
        image = var.image

        env {
          name  = "REVIEW_MODE"
          value = "diff"
        }
        env {
          name = "ANTHROPIC_API_KEY"
          value_source {
            secret_key_ref {
              secret  = var.anthropic_api_key_secret
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

        resources {
          limits = {
            cpu    = "2"
            memory = "4Gi"
          }
        }
      }

      max_retries = 0
    }
  }
}

resource "google_cloud_run_v2_job" "nightly_review_full" {
  name     = "nightly-review-full"
  location = var.region

  template {
    task_count = 1

    template {
      service_account = google_service_account.nightly_review.email
      timeout         = "3600s"

      containers {
        image = var.image

        env {
          name  = "REVIEW_MODE"
          value = "full"
        }
        env {
          name = "ANTHROPIC_API_KEY"
          value_source {
            secret_key_ref {
              secret  = var.anthropic_api_key_secret
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

        resources {
          limits = {
            cpu    = "2"
            memory = "4Gi"
          }
        }
      }

      max_retries = 0
    }
  }
}

# --- Cloud Scheduler ---

resource "google_cloud_scheduler_job" "nightly_review_diff" {
  name      = "nightly-review-diff"
  region    = var.region
  schedule  = "0 3 * * 0,1,2,4,5,6"
  time_zone = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.nightly_review_diff.name}:run"

    oauth_token {
      service_account_email = google_service_account.nightly_review.email
    }
  }
}

resource "google_cloud_scheduler_job" "nightly_review_full" {
  name      = "nightly-review-full"
  region    = var.region
  schedule  = "0 3 * * 3"
  time_zone = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.nightly_review_full.name}:run"

    oauth_token {
      service_account_email = google_service_account.nightly_review.email
    }
  }
}

# Cloud Run Jobs 実行権限を SA に付与
resource "google_cloud_run_v2_job_iam_member" "diff_invoker" {
  name     = google_cloud_run_v2_job.nightly_review_diff.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.nightly_review.email}"
}

resource "google_cloud_run_v2_job_iam_member" "full_invoker" {
  name     = google_cloud_run_v2_job.nightly_review_full.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.nightly_review.email}"
}
