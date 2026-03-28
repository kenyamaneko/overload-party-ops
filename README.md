# overload-party-ops

Overload Party の運用ジョブ・サービス管理リポジトリ。Cloud Run Jobs / Services で実行するコンテナイメージと GitHub Actions ワークフローを管理する。

## ジョブ一覧

| ジョブ | 説明 | ツール |
|--------|------|--------|
| `db-migrate` | Cloud SQL スキーママイグレーション + IAM 権限付与（破壊的変更の安全チェック付き） | psqldef, psql |
| `nightly-review` | 毎晩 3:00 (JST) に全リポジトリを自動レビュー → GitHub Issues 起票 | Claude Code, gh |
| `cost-monitor` | 毎朝 8:00 (JST) に dev/stg のコスト発生リソースをチェック → Slack 通知 | gcloud, kubectl |
| `drift-monitor` | 毎朝 7:00 (JST) に全リポジトリの Terraform plan を実行し drift 検出 → Slack 通知 | terraform, git |

## サービス一覧

| サービス | 説明 | ツール |
|---------|------|--------|
| `slack-commands` | Slack スラッシュコマンド（`/open-issues` 等）を処理する HTTP サービス | FastAPI, httpx |

## 使い方

### DB マイグレーション（手動）

GitHub Actions の `workflow_dispatch` から手動実行する。

1. **Actions** → **DB Migration** → **Run workflow**
2. `environment`: `dev` or `stg`
3. `dry_run`: `true` にするとイメージ更新のみ（ジョブ実行しない）

### DB マイグレーション（自動）

common リポの `db/schema_postgres.sql` または `db/grant_iam.sql` が main に push されると、`repository_dispatch` 経由で dev 環境に自動適用される。

### 仕組み

```
overload-party-common (db/)      overload-party-infra (Terraform)
        │                                 │
        │ SQL files                       │ Cloud Run Job リソース定義
        │ (sparse-checkout)               │ SA / IAM / Secret Manager
        ▼                                 │
overload-party-ops (ここ)                  │
        │                                 │
        │ Dockerfile + workflow           │
        │ イメージビルド & push            │
        ▼                                 ▼
   Artifact Registry ───────────→ Cloud Run Job (db-migrate)
                                     │
                                     │ Direct VPC Egress
                                     ▼
                                  Cloud SQL (Private IP)
```

## ディレクトリ構成

```
db-migrate/              # DB マイグレーションジョブ
  Dockerfile             # psqldef + psql イメージ
  entrypoint.sh          # マイグレーション実行スクリプト
  schema_check.py        # 破壊的変更検出（DROP TABLE/COLUMN）
nightly-review/          # 夜間自動レビュー（GitHub Actions schedule で実行）
  review.py              # メインスクリプト（差分レビュー・Issue 起票）
cost-monitor/            # 環境コスト監視（Cloud Run Job）
  Dockerfile             # gcloud SDK + kubectl イメージ
  check.py               # Cloud SQL, GKE, Ingress, IP, PSC チェック → Slack 通知
drift-monitor/           # Terraform drift 検出（Cloud Run Job）
  Dockerfile             # terraform + gcloud SDK + git イメージ
  check.py               # 全リポの terraform plan → drift 検出 → Slack 通知
slack-commands/          # Slack スラッシュコマンド（Cloud Run Service）
  Dockerfile             # Python 3.12 + FastAPI
  main.py                # FastAPI アプリ、コマンドディスパッチ
  adapters/              # 外部サービス連携（GitHub API, Worker 認証）
  routers/               # コマンドハンドラ（open_reviews 等）
slack-commands-worker/   # Cloudflare Worker（Slack 署名検証 + 即時応答）
  src/index.ts           # リクエスト受付・署名検証・Cloud Run への転送
  src/slack-verify.ts    # Slack 署名検証ロジック
  wrangler.toml          # Cloudflare Worker 設定
terraform/
  shared/                # 複数ジョブで共有する Secret（github-pat-nightly-review, github-pat-slack-commands）と IAM
    main.tf
    variables.tf
  nightly_review/        # 旧 Cloud Run Job 環境（terraform apply で destroy 後に削除予定）
    main.tf
    variables.tf
  cost_monitor/          # Cloud Run Job + Cloud Scheduler + SA + IAM
    main.tf
    variables.tf
  drift_monitor/         # Cloud Run Job + Cloud Scheduler + SA + IAM
    main.tf
    variables.tf
  slack_commands/        # Cloud Run Service + SA + IAM
    main.tf
    variables.tf
.github/workflows/
  build-deploy-job.yaml      # ジョブ共通ビルド・デプロイ (reusable workflow)
  build-deploy-service.yaml  # サービス共通ビルド・デプロイ (reusable workflow)
  nightly-review.yaml        # nightly-review の定時実行 + 手動実行
  cost-monitor.yaml          # cost-monitor のビルド・デプロイ
  drift-monitor.yaml         # drift-monitor のビルド・デプロイ
  slack-commands.yaml        # slack-commands のビルド・デプロイ
  slack-commands-worker.yaml # slack-commands-worker のデプロイ (wrangler deploy)
  db-migrate.yaml            # 手動 dispatch: ビルド → push → Cloud Run Job 実行
  db-migrate-on-push.yaml    # 自動: common の push で dev に適用
Makefile                     # ローカル開発用コマンド
```

## CI/CD

各ジョブ・サービスのディレクトリ配下を変更して main に push すると、自動でイメージビルド → AR push → Cloud Run 更新が実行される。

| 名前 | ワークフロー | トリガー |
|------|------------|---------|
| `db-migrate` | `db-migrate.yaml` | 手動 dispatch / common push（dev 自動） |
| `nightly-review` | `nightly-review.yaml` | 毎日 3:00 JST (schedule) / 手動 dispatch |
| `cost-monitor` | `cost-monitor.yaml` | main push (`cost-monitor/**`) / 手動 dispatch |
| `drift-monitor` | `drift-monitor.yaml` | main push (`drift-monitor/**`) / 手動 dispatch |
| `slack-commands` | `slack-commands.yaml` | main push (`slack-commands/**`) / 手動 dispatch |
| `slack-commands-worker` | `slack-commands-worker.yaml` | main push (`slack-commands-worker/**`) / 手動 dispatch |

## ローカル開発

```bash
# イメージビルド
make build-cost-monitor

# ビルド + AR push
make push-cost-monitor

# ビルド + push + Cloud Run Job 更新（デフォルト: overload-party-dev）
make deploy-cost-monitor

# ビルド + push + Cloud Run Service 更新
make deploy-service-slack-commands

# stg 環境にデプロイ
make deploy-cost-monitor PROJECT=overload-party-stg

# 全イメージビルド
make build-all

# コマンド一覧
make help
```

## Slack Commands セットアップ

初回のみ以下の手動手順が必要。

### アーキテクチャ

```
Slack → Cloudflare Worker (即時応答) → Cloud Run (処理実行) → response_url に結果POST
```

- Cloudflare Worker: Slack 署名検証 + 即時応答（コールドスタート回避）
- Cloud Run: コマンド処理（Worker からの Bearer トークンで認証）

### 1. Slack App 作成

1. https://api.slack.com/apps で **Create New App** → **From scratch**
2. App Name: `overload-party-ops`（今後他のコマンドも同じ App に追加する）
3. ワークスペースを選択して作成

### 2. Cloudflare API トークン作成

1. https://dash.cloudflare.com > My Profile > **API Tokens** > **Create Custom Token**
2. Permission: **Account / Workers Scripts / Edit**（Zone Resources は不要）
3. トークンと Account ID（Workers & Pages 画面の右サイドバー）を控える

### 3. Terraform apply

```bash
cd terraform/shared
terraform apply    # github-pat-nightly-review, github-pat-slack-commands の accessors に SA を追加

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

## 関連リポジトリ

| リポジトリ | 内容 |
|-----------|------|
| [overload-party-common](https://github.com/kenyamaneko/overload-party-common) | 共有データ（スキーマ SQL, カード定義, ドキュメント） |
| [overload-party-infra](https://github.com/kenyamaneko/overload-party-infra) | Terraform（Cloud Run Job / Cloud SQL / IAM） |

## ロールバック

psqldef は宣言的な DDL 適用ツールであり、自動ロールバック機能はない。スキーマ変更のロールバックが必要な場合は以下の手順で対応する。

### 前バージョンへのスキーマロールバック

1. 前のスキーマ状態を確認する:
   ```bash
   # common リポで前のスキーマを確認
   cd overload-party-common
   git log --oneline db/schema_postgres.sql
   git diff HEAD~1 db/schema_postgres.sql
   ```

2. 前のバージョンの SQL を使って psqldef を再実行する:
   ```bash
   # 前のコミットの schema_postgres.sql を取得
   git show HEAD~1:db/schema_postgres.sql > /tmp/schema_previous.sql

   # psqldef で適用（--dry-run で事前確認）
   psqldef --host=<HOST> --user=<USER> --password=<PASS> overload_party --dry-run < /tmp/schema_previous.sql
   psqldef --host=<HOST> --user=<USER> --password=<PASS> overload_party < /tmp/schema_previous.sql
   ```

3. GitHub Actions から実行する場合:
   - 前のコミットハッシュで `db-migrate.yaml` を `workflow_dispatch` 実行
   - `dry_run: true` で事前確認してから `dry_run: false` で適用

### データのロールバック（Cloud SQL バックアップから復元）

データの破損や誤削除が発生した場合は Cloud SQL のバックアップから復元する:

```bash
# バックアップ一覧の確認
gcloud sql backups list --instance=overload-party-db --project=<PROJECT>

# バックアップからの復元
gcloud sql backups restore <BACKUP_ID> --restore-instance=overload-party-db --project=<PROJECT>
```

### 緊急時の直接修正（Cloud SQL Auth Proxy 経由）

```bash
# Cloud SQL Auth Proxy の起動
cloud-sql-proxy <PROJECT>:<REGION>:overload-party-db --port=5432

# psql で直接接続して修正
psql -h 127.0.0.1 -U <USER> -d overload_party
```

> **注意:** 直接修正は psqldef の管理状態と乖離するため、修正後に `schema_postgres.sql` を必ず同期すること。
