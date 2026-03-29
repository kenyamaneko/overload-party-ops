# Nightly Shutdown

dev 環境のリソースを毎晩自動停止し、コストを削減するワークフロー。

## 停止対象リソース

| リソース | 操作 |
|---|---|
| Ingress & BackendConfig | `kubectl delete` で削除 |
| 予約済み外部 IP | Ingress 削除後に RESERVED 状態の IP を削除 |
| DNS (Cloudflare) | A レコードを `127.0.0.1` に変更 |
| Pod (gateway, battle) | レプリカ数を 0 にスケール |
| PSC フォワーディングルール | `cloudsql-psc-{env}` を削除 |
| Cloud SQL | activation policy を `NEVER` に変更して停止 |

## スケジュール

- **定期実行**: 毎日 2:00 AM JST（dev 環境のみ）
- **手動実行**: `workflow_dispatch` から dev / stg を選択して実行可能

## Slack 通知

実行結果を Slack に通知する。成功時・失敗時ともに通知され、失敗時はログへのリンクが含まれる。

## セットアップ

### Secrets

| 名前 | 用途 |
|---|---|
| `SLACK_WEBHOOK_URL` | Slack 通知用 Webhook URL |
| `CLOUDFLARE_DNS_API_TOKEN` | Cloudflare API トークン（DNS 編集権限） |

### Variables

| 名前 | 用途 |
|---|---|
| `WIF_PROVIDER` | Workload Identity Federation プロバイダ |
| `CI_SERVICE_ACCOUNT` | CI 用サービスアカウント |
| `CLOUDFLARE_ZONE_ID` | Cloudflare ゾーン ID |
| `CLOUDFLARE_DNS_RECORD_ID_DEV` | dev 環境の DNS レコード ID |
| `CLOUDFLARE_DNS_RECORD_ID_STG` | stg 環境の DNS レコード ID |
