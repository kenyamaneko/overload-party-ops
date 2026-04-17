# drift-monitor

対象リポジトリの Terraform plan を実行し、インフラの drift（実態と定義のずれ）を検出して Slack に通知する。

## 動作フロー

1. `targets.yaml` で定義された各リポジトリを PAT を使って shallow clone
2. 環境ごとに `terraform init` + `terraform plan -detailed-exitcode` を実行
3. exit code 2（差分あり）→ drift として報告、exit code 1（エラー）→ エラーとして報告
4. drift またはエラーがあれば Slack に通知（全環境クリアなら通知しない）

plan 出力からは `Plan:` 行と変更対象リソース（最大 10 件）を抽出してサマリとする。

## スケジュール

毎日 7:00 AM JST（cron: `0 22 * * *` UTC）。手動実行（`workflow_dispatch`）も可能。

## 設定

### targets.yaml

監視対象のリポジトリと Terraform 環境パスを定義する。

```yaml
- repo: overload-party-infra
  environments:
    - name: google-cloud/platform
      path: providers/google-cloud/platform
    - name: google-cloud/dev
      path: providers/google-cloud/env/dev
    - name: google-cloud/stg
      path: providers/google-cloud/env/stg
    - name: google-cloud/prod
      path: providers/google-cloud/env/prod
    - name: cloudflare
      path: providers/cloudflare
    - name: upstash/dev
      path: providers/upstash/env/dev
    - name: upstash/stg
      path: providers/upstash/env/stg
    - name: upstash/prod
      path: providers/upstash/env/prod

- repo: overload-party-ops
  environments:
    - name: shared
      path: terraform/shared
```

`TARGETS_JSON` 環境変数が設定されている場合はそちらが優先される。

## Slack 通知

- drift 検出時: `:rotating_light: Terraform Drift — 差分を検出` + 対象ラベルとサマリ
- plan エラー時: `:warning: Terraform Drift — plan 実行エラー` + エラー詳細
- 全環境クリア: 通知なし

メッセージは 3,900 文字を超えた場合に切り詰められる。

## 必要なシークレット / 変数

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `SLACK_WEBHOOK_URL` | Slack 通知用 Webhook URL |
| Secret | `GH_PAT_NIGHTLY_REVIEW` | リポジトリ clone 用 Personal Access Token（`GITHUB_TOKEN` として注入） |
| Variable | `WIF_PROVIDER` | Workload Identity Federation プロバイダ |
| Variable | `CI_SERVICE_ACCOUNT` | Google Cloud サービスアカウント |
