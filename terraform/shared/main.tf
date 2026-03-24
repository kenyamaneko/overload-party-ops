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

# --- 共有 Secret: github-pat-nightly-review ---

resource "google_secret_manager_secret" "github_pat_nightly_review" {
  secret_id = "github-pat-nightly-review"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "github_pat_nightly_review" {
  for_each  = toset(var.github_pat_nightly_review_accessors)
  secret_id = google_secret_manager_secret.github_pat_nightly_review.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}

# --- 共有 Secret: github-pat-slack-commands ---

resource "google_secret_manager_secret" "github_pat_slack_commands" {
  secret_id = "github-pat-slack-commands"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "github_pat_slack_commands" {
  for_each  = toset(var.github_pat_slack_commands_accessors)
  secret_id = google_secret_manager_secret.github_pat_slack_commands.secret_id
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
