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

# --- APIs (keyandnotes-ops プロジェクト共通) ---

resource "google_project_service" "secretmanager" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "run" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudscheduler" {
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
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

# --- GitHub Actions deploy SA への IAM 付与 ---
# cost-monitor, drift-monitor 等が GitHub Actions 上で直接実行されるため、
# deploy SA に必要な権限を付与する。

# cost-monitor: Cloud SQL 状態確認
resource "google_project_iam_member" "deploy_cloudsql_viewer" {
  for_each = toset(var.cost_monitor_projects)
  project  = each.value
  role     = "roles/cloudsql.viewer"
  member   = "serviceAccount:${var.deploy_service_account}"
}

# cost-monitor: Compute リソース（Static IP, PSC）確認
resource "google_project_iam_member" "deploy_compute_viewer" {
  for_each = toset(var.cost_monitor_projects)
  project  = each.value
  role     = "roles/compute.viewer"
  member   = "serviceAccount:${var.deploy_service_account}"
}

# cost-monitor: GKE Deployment/Ingress 確認
resource "google_project_iam_member" "deploy_gke_viewer" {
  project = var.gke_project
  role    = "roles/container.viewer"
  member  = "serviceAccount:${var.deploy_service_account}"
}

# cost-monitor: gcloud sql コマンドに必要
resource "google_project_service" "sqladmin" {
  service            = "sqladmin.googleapis.com"
  disable_on_destroy = false
}

# drift-monitor: Terraform state bucket 読み取り
resource "google_storage_bucket_iam_member" "deploy_tf_state_reader" {
  bucket = var.tf_state_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.deploy_service_account}"
}

# drift-monitor: terraform plan に必要（各監視対象プロジェクト）
resource "google_project_iam_member" "deploy_viewer" {
  for_each = toset(var.drift_monitor_projects)
  project  = each.value
  role     = "roles/viewer"
  member   = "serviceAccount:${var.deploy_service_account}"
}

# drift-monitor: バケット IAM ポリシー読み取り
resource "google_project_iam_member" "deploy_security_reviewer" {
  for_each = toset(var.drift_monitor_projects)
  project  = each.value
  role     = "roles/iam.securityReviewer"
  member   = "serviceAccount:${var.deploy_service_account}"
}

# drift-monitor: infra リポの plan で必要
resource "google_project_service" "firebase" {
  service            = "firebase.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "firebase_hosting" {
  service            = "firebasehosting.googleapis.com"
  disable_on_destroy = false
}
