# Nightly Review

Claude Code を使った夜間自動レビューシステム。各リポジトリの前日との差分をレビューし、結果を GitHub Issues に起票する。

## 設定

`repos.yaml` でリポジトリ名と対象ブランチを管理する。

```yaml
- name: overload-party-common
  branch: main
- name: overload-party-client
  branch: main
```

- `branch` は必須。未設定のリポジトリはスキップされ、Slack にエラー通知される
- Cloud Run Jobs のコンテナに `repos.yaml` ごと焼き込んで配布する（SSoT 一元化のため環境変数オーバーライドは廃止）

## レビュー観点

`review_criteria.yaml` でカテゴリ別にレビュー観点を管理する。プロンプト上では `## {カテゴリ名}` セクションとして展開される。

```yaml
categories:
  - name: 設計
    items:
      - 設計通りに実装されているか（...）
      - 拡張性と保守性が高い設計であること
```

- `categories` が空、または各カテゴリの `items` が空の場合はエラーで止まる（意図しない空観点でのレビュー実行防止）

## 必要なシークレット / 変数

ops リポジトリの Settings > Secrets and variables > Actions:

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | Anthropic API キー |
| Secret | `GH_PAT_NIGHTLY_REVIEW` | 他リポの Issue を作成するための fine-grained PAT |
| Secret | `SLACK_WEBHOOK_URL` | Slack 通知用 Webhook URL |

## Slack 通知

- レビューコメントがある場合: Issue 作成後に Slack 通知
- branch 未設定のリポ: 設定エラーとして Slack 通知
- ジョブ失敗時: Slack 通知

## GitHub Issues

- 起票先: 各対象リポジトリ
- タイトル: `[自動レビュー YYYY-MM-DD] 差分 {リポジトリ名}`
- ラベル: `auto-review`
- 同日・同リポジトリの Issue が既に存在する場合はスキップ
