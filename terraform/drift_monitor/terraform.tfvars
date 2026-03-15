project_id = "keyandnotes-ops"
image      = "asia-northeast1-docker.pkg.dev/keyandnotes-platform/overload-party/drift-monitor:latest"

targets = [
  {
    repo = "overload-party-infra"
    environments = [
      { name = "dev", path = "environments/dev", project = "overload-party-dev" },
      { name = "stg", path = "environments/stg", project = "overload-party-stg" },
      { name = "prod", path = "environments/prod", project = "overload-party-prod" },
    ]
  },
  {
    repo = "overload-party-k8s"
    environments = [
      { name = "platform", path = "terraform/environments/platform", project = "keyandnotes-platform" },
    ]
  },
  {
    repo = "overload-party-ops"
    environments = [
      { name = "nightly-review", path = "terraform/nightly_review", project = "keyandnotes-ops" },
      { name = "cost-monitor", path = "terraform/cost_monitor", project = "keyandnotes-ops" },
      { name = "drift-monitor", path = "terraform/drift_monitor", project = "keyandnotes-ops" },
      { name = "shared", path = "terraform/shared", project = "keyandnotes-ops" },
    ]
  },
  # Cloud Function のデプロイ準備ができたら有効化する
  # {
  #   repo = "overload-party-analytics"
  #   environments = [
  #     { name = "dev", path = "terraform/environments/dev", project = "overload-party-dev" },
  #   ]
  # },
]
