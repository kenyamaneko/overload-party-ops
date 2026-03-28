# Slack Commands

Slack スラッシュコマンドを処理する HTTP サービス。Cloudflare Worker 経由でリクエストを受け取り、各コマンドに応じた処理を実行して結果を Slack に返す。

## 対応コマンド

| コマンド | 説明 |
|---------|------|
| `/open-issues` | 未クローズの自動レビュー Issue を一覧表示 |
| `/db-start` | Cloud SQL インスタンスを起動 |
| `/db-stop` | Cloud SQL インスタンスを停止 |
| `/gke-up` | GKE 環境を起動（Ingress, Deployment 等） |
| `/gke-down` | GKE 環境を停止 |
| `/publish-gamedata-pkg` | ゲームデータパッケージの publish ワークフローをディスパッチ |

## アーキテクチャ

```
Slack → Cloudflare Worker (即時応答) → Cloud Run (処理実行) → response_url に結果POST
```

- **Cloudflare Worker** (`slack-commands-worker/`): Slack 署名検証 + 即時応答（コールドスタート回避）
- **Cloud Run Service** (ここ): コマンド処理。Worker からの Bearer トークンで認証

## デプロイ

`slack-commands/` 配下を main に push すると自動デプロイされる。手動実行は `workflow_dispatch` から。

## 初回セットアップ

### 1. Slack App 作成

1. https://api.slack.com/apps で **Create New App** → **From scratch**
2. App Name: `overload-party-ops`
3. ワークスペースを選択して作成

### 2. Cloudflare API トークン作成

1. https://dash.cloudflare.com > My Profile > **API Tokens** > **Create Custom Token**
2. Permission: **Account / Workers Scripts / Edit**（Zone Resources は不要）
3. トークンと Account ID（Workers & Pages 画面の右サイドバー）を控える

### 3. Terraform apply

```bash
cd terraform/shared
terraform apply    # deploy SA の IAM + 共有 Secret

cd ../slack_commands
terraform init
terraform apply    # Cloud Run Service, SA, Secret, IAM, dispatch-secret を作成
```

### 4. Secrets 登録

**Slack Signing Secret** — Slack App > Basic Information > App Credentials からコピー:

```bash
echo -n '<Signing Secret>' | gcloud secrets versions add slack-signing-secret --data-file=- --project=keyandnotes-ops
```

**DISPATCH_SECRET** — Worker ↔ Cloud Run 間の共有トークンを生成して登録:

```bash
openssl rand -base64 32
# ↑ の出力をメモ（Cloudflare Worker にも同じ値を登録する）

echo -n '<生成したトークン>' | gcloud secrets versions add slack-commands-dispatch-secret --data-file=- --project=keyandnotes-ops
```

### 5. GitHub Secrets / Variables 登録

ops リポジトリの Settings > Secrets and variables > Actions:

| 種別 | 名前 | 値 |
|---|---|---|
| Secret | `CLOUDFLARE_WORKERS_API_TOKEN` | 手順 2 の API トークン |
| Variable | `CLOUDFLARE_ACCOUNT_ID` | 手順 2 の Account ID |

### 6. Cloud Run デプロイ

```bash
make deploy-service-slack-commands
```

### 7. Cloudflare Worker デプロイ

```bash
cd slack-commands-worker
npm ci

CLOUDFLARE_API_TOKEN="<手順 2 のトークン>" \
CLOUDFLARE_ACCOUNT_ID="<手順 2 の Account ID>" \
npx wrangler deploy
```

Worker の secrets を登録（対話形式で値を入力）:

```bash
npx wrangler secret put SLACK_SIGNING_SECRET   # Slack App の Signing Secret
npx wrangler secret put DISPATCH_SECRET         # 手順 4 で生成した共有トークン
```

以降のデプロイは `main` push で GitHub Actions が自動実行する。

### 8. スラッシュコマンド登録

1. Slack App の左メニュー **Slash Commands** → **Create New Command**
2. Command: `/open-issues`
3. Request URL: `https://slack-commands-proxy.<サブドメイン>.workers.dev/slack/commands`
4. Short Description: `未クローズの自動レビュー Issue を一覧表示`
5. **Install to Workspace** で App をインストール

全コマンドの Request URL は同じ Worker URL を指定する。
