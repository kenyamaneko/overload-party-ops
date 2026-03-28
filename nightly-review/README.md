# Nightly Review

Claude Code を使った夜間自動レビューシステム。GitHub Actions schedule で毎晩 3:00 (JST) に各リポジトリをレビューし、結果を GitHub Issues に起票する。

## スケジュール

毎晩 3:00 (JST) に各リポジトリの前日との差分をレビューする。

## セットアップ

### 1. GitHub Secrets に登録

ops リポジトリの Settings > Secrets and variables > Actions > Secrets:

| 名前 | 値 |
|------|-----|
| `ANTHROPIC_API_KEY` | Anthropic API キー |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |
| `GITHUB_TOKEN` | 他リポの Issue を作成するため PAT が必要 |

### 2. （任意）対象リポジトリの変更

GitHub Actions variables の `REPOS_JSON` を変更する（JSON 配列形式）。省略時はワークフロー内のデフォルト値が使われる。

## Slack 通知

- レビューコメントがある場合: Issue 作成後に Slack 通知
- ジョブ失敗時: Slack 通知

## GitHub Issues

- 起票先: 各対象リポジトリ
- タイトル: `[自動レビュー YYYY-MM-DD] 差分 {リポジトリ名}`
- ラベル: `auto-review`
- 同日・同リポジトリの Issue が既に存在する場合はスキップ
