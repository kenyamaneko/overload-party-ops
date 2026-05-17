# drift-monitor

対象リポジトリの Terraform plan を実行し、インフラの drift（実態と定義のずれ）を検出して Slack に通知する。

## 動作フロー

1. `targets.yaml` で定義された各リポジトリを GitHub App (Ops Automation) の短命 token で shallow clone
2. 環境ごとに `terraform init` + `terraform plan -out=... -detailed-exitcode` を実行
3. exit code 2（差分あり）→ `terraform show -json` で構造化 JSON を取得
4. `targets.yaml` の `suppress:` ルールに当てはまる属性差分だけを持つリソースは drift から除外
5. 除外後に残った差分を drift として報告、exit code 1（エラー）→ エラーとして報告
6. 実行結果を Slack に通知（drift / エラーがあればその内容、全環境クリアまたは全差分が suppress で吸収されたなら「差分なし」）

サマリは変更対象リソースを最大 10 件まで載せ、超過分は集約行にまとめる。

手動実行（`workflow_dispatch`）も可能。

## 設定

### targets.yaml

監視対象のリポジトリと Terraform 環境パスを定義する。

```yaml
- repo: overload-party-infra
  environments:
    - name: google-cloud/dev
      path: providers/google-cloud/env/dev
      suppress:
        - type: google_sql_database_instance
          attribute: settings[0].activation_policy
```

### suppress の意味

`suppress` は env 単位の drift 抑止ルール。各ルールは
`{type, attribute}` のペアで、「このリソース型のこの属性だけが差分になっている
update」を drift として通知しない。

現在の主用途: dev/stg の Cloud SQL `activation_policy` は nightly-shutdown や
Slack の `/db-stop`・`/db-start` で Terraform 外から書き換えるため、差分が
出るのは日常運用。prod は suppress を設定しないので、同じ属性でも drift として
通知される（= 意図しない停止を検知できる）。

ルールは以下の条件すべてを満たすときだけ抑止に使われる:

- `actions == ["update"]`（create / delete / replace のような構造的変更は常に通知）
- リソースの `type` がルールと一致
- 変更されている**全属性**がルール側の attribute に含まれる（1 つでも範囲外が
  あればリソースごと visible に残す）

## Slack 通知

毎朝必ず 1 通通知する（死活確認を兼ねるため、全環境クリアでも送る）。

- drift 検出時: `:rotating_light: [ドリフト警告 {日付}] 差分を検出` + 対象ラベルとサマリ
- plan エラー時: `:warning: [ドリフト確認 {日付}] plan 実行エラー` + エラー詳細
- 全環境クリア: `:white_check_mark: [ドリフト確認 {日付}] 差分なし`

メッセージは 3,900 文字を超えた場合に切り詰められる。

## 必要なシークレット / 変数

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `SLACK_WEBHOOK_URL` | Slack 通知用 Webhook URL |
| Variable | `OPS_AUTOMATION_APP_ID` | リポジトリ clone 用 GitHub App (Ops Automation) の App ID |
| Secret | `OPS_AUTOMATION_APP_PRIVATE_KEY` | 同 App の秘密鍵。短命 token を発行し `GITHUB_TOKEN` として注入 |
| Variable | `WIF_PROVIDER` | Workload Identity Federation プロバイダ |
| Variable | `CI_SERVICE_ACCOUNT` | Google Cloud サービスアカウント |
| Secret | `CLOUDFLARE_CDN_API_TOKEN` | Cloudflare 環境の plan 用 API トークン |
| Variable | `UPSTASH_EMAIL` | Upstash 環境の plan 用アカウントメール |
| Secret | `UPSTASH_API_KEY` | Upstash 環境の plan 用 API キー |
