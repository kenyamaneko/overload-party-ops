project_id = "keyandnotes-ops"

deploy_service_account = "github-deploy@keyandnotes-platform.iam.gserviceaccount.com"

github_pat_nightly_review_accessors = []

github_pat_slack_commands_accessors = [
  "serviceAccount:slack-commands@keyandnotes-ops.iam.gserviceaccount.com",
]

slack_webhook_url_accessors = [
  "serviceAccount:slack-commands@keyandnotes-ops.iam.gserviceaccount.com",
]

cost_monitor_projects = [
  "overload-party-dev",
  "overload-party-stg",
]

drift_monitor_projects = [
  "overload-party-dev",
  "overload-party-stg",
  "overload-party-prod",
  "keyandnotes-ops",
  "keyandnotes-platform",
]
