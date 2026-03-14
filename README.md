# overload-party-ops

Overload Party の運用ジョブ管理リポジトリ。Cloud Run Jobs で実行するコンテナイメージと GitHub Actions ワークフローを管理する。

## ジョブ一覧

| ジョブ | 説明 | ツール |
|--------|------|--------|
| `db-migrate` | Cloud SQL スキーママイグレーション + IAM 権限付与 | psqldef, psql |
| `nightly-review` | 毎晩 3:00 (JST) に全リポジトリを自動レビュー → GitHub Issues 起票 | Claude Code, gh |

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
nightly-review/          # 夜間自動レビュージョブ
  Dockerfile             # Node.js 22 + Claude Code + gh
  review.py              # メインスクリプト（差分/全体の分岐・Issue 起票）
terraform/
  nightly_review/        # Cloud Run Job + Cloud Scheduler + SA + IAM
    main.tf
    variables.tf
.github/workflows/
  db-migrate.yaml        # 手動 dispatch: ビルド → push → Cloud Run Job 実行
  db-migrate-on-push.yaml  # 自動: common の push で dev に適用
```

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
