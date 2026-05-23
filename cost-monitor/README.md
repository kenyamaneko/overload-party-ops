# cost-monitor

dev/stg 環境でコストが発生しているリソースを検出し、Slack に通知する。

## チェック対象

| リソース | 検出条件 | 参考コスト |
|---|---|---|
| Cloud SQL | state が `RUNNABLE` | $0.19/hr |
| GKE node pool (`keyandnotes-main-{env}`) | instance group の `targetSize` > 0 | ノードタイプ依存 |
| Ingress (`overload-party`) | LB IP が割り当て済み | ~$0.025/hr |
| 予約済み外部 IP | status=RESERVED, addressType=EXTERNAL | ~$3.65/mo |
| PSC forwarding rule | target が serviceAttachments | - |

node pool は共有クラスタ `keyandnotes-main` (`keyandnotes-platform` プロジェクト) の env 別 node pool を見る。env-lifecycle.yaml の shutdown は node pool resize 方式で行うため、Deployment.spec.replicas ではなく実コストドライバである instance group の `targetSize` で稼働判定する。Ingress は namespace に依存するため namespace が存在しない環境ではスキップされる。

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

- コスト発生リソースなし: `:white_check_mark: 稼働中リソースなし`
- コスト発生リソースあり: `:warning: コスト警告` + 環境ごとの検出リソース一覧
- チェックエラー: `:x: チェックエラー` として別セクションで報告（ワークフローも失敗終了）

## 必要なシークレット / 変数

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `SLACK_WEBHOOK_URL` | Slack 通知用 Webhook URL |
| Variable | `WIF_PROVIDER` | Workload Identity Federation プロバイダ |
| Variable | `CI_SERVICE_ACCOUNT` | Google Cloud サービスアカウント |
