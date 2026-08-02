# cost-monitor

dev/stg 環境でコストが発生しているリソースを検出し、Slack に通知する。

## チェック対象

| リソース | 検出条件 | 通知に載せる情報 |
|---|---|---|
| Cloud SQL | state が `RUNNABLE` | マシンタイプ (`db-g1-small` 等) |
| 予約済み外部 IP | status=RESERVED, addressType=EXTERNAL | ~$3.65/mo |
| PSC forwarding rule | target が serviceAttachments | - |

Cloud SQL はマシンタイプを変えると単価が変わるため、金額ではなくマシンタイプを載せる。

Cloud Run は使わないあいだインスタンス数が 0 になり課金されないため、検出対象に含めない。

手動実行（`workflow_dispatch`）も可能。

## 設定

### environments.yaml

監視対象の環境名と Google Cloud プロジェクト ID のマッピングを定義する。

```yaml
dev: overload-party-dev
stg: overload-party-stg
```

`ENVIRONMENTS_JSON` 環境変数が設定されている場合はそちらが優先される。

## Slack 通知

- コスト発生リソースなし: `:white_check_mark: *[コスト確認 {日付}] 稼働中リソースなし*`
- コスト発生リソースあり: `:warning: *[コスト警告 {日付}] 稼働中リソースあり*` + 環境ごとの検出リソース一覧
- チェックエラー: `:x: *[コスト確認 {日付}] チェックエラー*` + Actions ログ URL を別セクションで報告（ワークフローも失敗終了）

## 必要なシークレット / 変数

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `SLACK_WEBHOOK_URL` | Slack 通知用 Webhook URL |
| Variable | `WIF_PROVIDER` | Workload Identity Federation プロバイダ |
| Variable | `CI_SERVICE_ACCOUNT` | Google Cloud サービスアカウント |
