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

# --- 共有 Secret: github-pat-ro (read-only PAT) ---

resource "google_secret_manager_secret" "github_pat_ro" {
  secret_id = "github-pat-ro"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "github_pat_ro" {
  for_each  = toset(var.github_pat_ro_accessors)
  secret_id = google_secret_manager_secret.github_pat_ro.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}

# --- 共有 Secret: github-pat-rw (read-write PAT) ---

resource "google_secret_manager_secret" "github_pat_rw" {
  secret_id = "github-pat-rw"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "github_pat_rw" {
  for_each  = toset(var.github_pat_rw_accessors)
  secret_id = google_secret_manager_secret.github_pat_rw.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}

# --- 共有 Secret: slack-webhook-url ---

resource "google_secret_manager_secret" "slack_webhook_url" {
  secret_id = "slack-webhook-url"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "slack_webhook_url" {
  for_each  = toset(var.slack_webhook_url_accessors)
  secret_id = google_secret_manager_secret.slack_webhook_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}
