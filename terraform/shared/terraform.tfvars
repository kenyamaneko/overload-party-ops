project_id = "keyandnotes-ops"

github_pat_ro_accessors = [
  "serviceAccount:nightly-review@keyandnotes-ops.iam.gserviceaccount.com",
  "serviceAccount:drift-monitor@keyandnotes-ops.iam.gserviceaccount.com",
]

github_pat_rw_accessors = [
  "serviceAccount:slack-commands@keyandnotes-ops.iam.gserviceaccount.com",
]

slack_webhook_url_accessors = [
  "serviceAccount:nightly-review@keyandnotes-ops.iam.gserviceaccount.com",
  "serviceAccount:cost-monitor@keyandnotes-ops.iam.gserviceaccount.com",
  "serviceAccount:drift-monitor@keyandnotes-ops.iam.gserviceaccount.com",
]
