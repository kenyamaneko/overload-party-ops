project_id = "overload-party-ops"

deploy_service_account = "github-ci@keyandnotes-platform.iam.gserviceaccount.com"

github_pat_nightly_review_accessors = [
  "serviceAccount:nightly-reviewer@overload-party-ops.iam.gserviceaccount.com",
]

github_pat_slack_commands_accessors = [
  "serviceAccount:slack-commands@overload-party-ops.iam.gserviceaccount.com",
]

slack_webhook_url_accessors = [
  "serviceAccount:slack-commands@overload-party-ops.iam.gserviceaccount.com",
  "serviceAccount:nightly-reviewer@overload-party-ops.iam.gserviceaccount.com",
]

cost_monitor_projects = [
  "overload-party-dev",
  "overload-party-stg",
]

drift_monitor_projects = [
  "overload-party-dev",
  "overload-party-stg",
  "overload-party-prod",
  "overload-party-ops",
  "keyandnotes-platform",
]
