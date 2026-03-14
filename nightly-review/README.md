# Nightly Review

Claude Code を使った夜間自動レビューシステム。Cloud Scheduler + Cloud Run Jobs で毎晩 3:00 (JST) に各リポジトリをレビューし、結果を GitHub Issues に起票する。

## スケジュール

| 曜日 | モード | 内容 |
|------|--------|------|
| 水曜以外 | 差分レビュー | GitHub API で前日との diff を取得 |
| 水曜 | 全体レビュー | git clone してリポジトリ全体を精読 |

## セットアップ

### 1. Secret Manager にシークレットを登録

```bash
echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-
echo -n "ghp_..." | gcloud secrets create github-token --data-file=-
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

## 対象リポジトリの変更

[review.py](review.py) 内の `REPOS` リストを編集してリビルドしてください。

## GitHub Issues

- 起票先: 各対象リポジトリ
- タイトル: `[自動レビュー YYYY-MM-DD] {差分|全体} {リポジトリ名}`
- ラベル: `auto-review`（差分）/ `auto-review-full`（全体）
- 同日・同リポジトリの Issue が既に存在する場合はスキップ
