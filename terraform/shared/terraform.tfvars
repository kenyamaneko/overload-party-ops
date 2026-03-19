project_id = "keyandnotes-ops"

github_token_accessors = [
  "serviceAccount:nightly-review@keyandnotes-ops.iam.gserviceaccount.com",
  "serviceAccount:drift-monitor@keyandnotes-ops.iam.gserviceaccount.com",
  "serviceAccount:slack-commands@keyandnotes-ops.iam.gserviceaccount.com",
]

slack_webhook_url_accessors = [
  "serviceAccount:nightly-review@keyandnotes-ops.iam.gserviceaccount.com",
  "serviceAccount:cost-monitor@keyandnotes-ops.iam.gserviceaccount.com",
  "serviceAccount:drift-monitor@keyandnotes-ops.iam.gserviceaccount.com",
]
