# Nightly Review

Claude Code を使った夜間自動レビューシステム。Cloud Scheduler + Cloud Run Jobs で毎晩 3:00 (JST) に各リポジトリをレビューし、結果を GitHub Issues に起票する。

## スケジュール

毎晩 3:00 (JST) に各リポジトリの前日との差分をレビューする。

## セットアップ

### 1. Secret Manager にシークレットを登録

```bash
echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-
echo -n "ghp_..." | gcloud secrets create github-pat-nightly-review --data-file=-
echo -n "https://hooks.slack.com/..." | gcloud secrets create slack-webhook-nightly-review --data-file=-
```

### 2. Docker イメージをビルド・プッシュ

```bash
cd nightly-review
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/PROJECT_ID/REPO/nightly-review:latest
```

### 3. Terraform で環境構築

```bash
cd terraform/nightly_review
terraform init
terraform apply \
  -var="project_id=YOUR_PROJECT_ID" \
  -var="image=asia-northeast1-docker.pkg.dev/PROJECT_ID/REPO/nightly-review:latest"
```

必須変数は `project_id` と `image`。その他の変数（`anthropic_api_key_secret`、`github_token_secret`、`slack_webhook_secret`、`repos`）はデフォルト値あり。詳細は [variables.tf](../terraform/nightly_review/variables.tf) を参照。

## 対象リポジトリの変更

Terraform の `repos` 変数を変更してください。デフォルト値は [variables.tf](../terraform/nightly_review/variables.tf) で定義されています。

## GitHub Issues

- 起票先: 各対象リポジトリ
- タイトル: `[自動レビュー YYYY-MM-DD] 差分 {リポジトリ名}`
- ラベル: `auto-review`
- 同日・同リポジトリの Issue が既に存在する場合はスキップ
