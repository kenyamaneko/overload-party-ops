# Slack Commands Worker

Slack スラッシュコマンドのリクエストを受け付ける Cloudflare Worker。Slack の署名を検証し、即時応答を返した上で Cloud Run にリクエストを転送する。

## なぜ Worker を挟むのか

Slack のスラッシュコマンドは **3 秒以内に HTTP 200 を返さなければタイムアウト**する。Cloud Run Service はコールドスタート時にコンテナ起動が 3 秒を超えることがあり、直接 Slack から呼ぶとタイムアウトになる。

Cloudflare Worker はコールドスタートがほぼゼロなので、即座に 200 を返してから `ctx.waitUntil()` でバックグラウンドで Cloud Run に転送する。Cloud Run 側は `response_url` に処理結果を POST して Slack に返す。

## 処理フロー

```
Slack
  │ POST /slack/commands
  ▼
Cloudflare Worker
  1. Slack 署名検証
  2. 即時応答「コマンドを受け付けました」(200)
  3. waitUntil で Cloud Run に転送
  │
  ▼
Cloud Run (slack-commands)
  1. Bearer トークン（DISPATCH_SECRET）で認証
  2. コマンド処理
  3. response_url に結果を POST
  │
  ▼
Slack（結果表示）
```

## デプロイ

`slack-commands-worker/` 配下を main に push すると GitHub Actions が自動デプロイする。

## 設定

`wrangler.toml`:

| 変数 | 用途 |
|---|---|
| `CLOUD_RUN_URL` | 転送先の Cloud Run Service URL |

## 必要なシークレット / 変数

Cloudflare Worker secrets（`wrangler secret put` で登録）:

| 名前 | 用途 |
|---|---|
| `SLACK_SIGNING_SECRET` | Slack App の Signing Secret |
| `DISPATCH_SECRET` | Cloud Run との共有認証トークン |

ops リポジトリの Settings > Secrets and variables > Actions:

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `CLOUDFLARE_WORKERS_API_TOKEN` | Cloudflare API トークン（Workers Scripts / Edit） |
| Variable | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID |
