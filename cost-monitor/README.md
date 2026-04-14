# cost-monitor

dev/stg 環境でコストが発生しているリソースを検出し、Slack に通知する。

## チェック対象

| リソース | 検出条件 | 参考コスト |
|---|---|---|
| Cloud SQL | state が `RUNNABLE` | $0.19/hr |
| GKE Deployment (`gateway`, `battle`) | replicas > 0 | - |
| Ingress (`overload-party`) | LB IP が割り当て済み | ~$0.025/hr |
| 予約済み外部 IP | status=RESERVED, addressType=EXTERNAL | ~$3.65/mo |
| PSC forwarding rule | target が serviceAttachments | - |

GKE 関連チェック（Deployment, Ingress）は共有クラスタ `keyandnotes-main`（`keyandnotes-platform` プロジェクト）に対して実行する。namespace が存在しない環境はスキップされる。

## スケジュール

毎日 8:00 AM JST（cron: `0 23 * * *` UTC）。手動実行（`workflow_dispatch`）も可能。

## 設定

### environments.yaml

監視対象の環境名と Google Cloud プロジェクト ID のマッピングを定義する。

```yaml
dev: overload-party-dev
stg: overload-party-stg
```

`ENVIRONMENTS_JSON` 環境変数が設定されている場合はそちらが優先される。

## Slack 通知

- コスト発生リソースなし: `:white_check_mark: 稼働中リソースなし`
- コスト発生リソースあり: `:warning: コスト警告` + 環境ごとの検出リソース一覧
- チェックエラー: `:x: チェックエラー` として別セクションで報告（ワークフローも失敗終了）

## 必要なシークレット / 変数

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `SLACK_WEBHOOK_URL` | Slack 通知用 Webhook URL |
| Variable | `WIF_PROVIDER` | Workload Identity Federation プロバイダ |
| Variable | `CI_SERVICE_ACCOUNT` | Google Cloud サービスアカウント |
