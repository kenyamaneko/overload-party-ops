# Nightly Review

Claude Code を使った夜間自動レビューシステム。GitHub Actions schedule で毎晩 3:00 (JST) に各リポジトリをレビューし、結果を GitHub Issues に起票する。

## スケジュール

毎晩 3:00 (JST) に各リポジトリの前日との差分をレビューする。

## 対象リポジトリの設定

`repos.yaml` でリポジトリ名と対象ブランチを管理する。

```yaml
- name: overload-party-common
  branch: main
- name: overload-party-client
  branch: main
```

- `branch` は必須。未設定のリポジトリはスキップされ、Slack にエラー通知される
- `REPOS_JSON` 環境変数で同形式の JSON を渡すとオーバーライド可能

## セットアップ

ops リポジトリの Settings > Secrets and variables > Actions:

| 種別 | 名前 | 値 |
|------|------|-----|
| Secret | `ANTHROPIC_API_KEY` | Anthropic API キー |
| Secret | `GH_PAT_NIGHTLY_REVIEW` | 他リポの Issue を作成するための Fine-grained PAT |
| Secret | `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

## Slack 通知

- レビューコメントがある場合: Issue 作成後に Slack 通知
- branch 未設定のリポ: 設定エラーとして Slack 通知
- ジョブ失敗時: Slack 通知

## GitHub Issues

- 起票先: 各対象リポジトリ
- タイトル: `[自動レビュー YYYY-MM-DD] 差分 {リポジトリ名}`
- ラベル: `auto-review`
- 同日・同リポジトリの Issue が既に存在する場合はスキップ
