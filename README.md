# overload-party-ops

Overload Party の運用ジョブ管理リポジトリ。Cloud Run Jobs で実行するコンテナイメージと GitHub Actions ワークフローを管理する。

## ジョブ一覧

| ジョブ | 説明 | ツール |
|--------|------|--------|
| `db-migrate` | Cloud SQL スキーママイグレーション + IAM 権限付与 | psqldef, psql |

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
.github/workflows/
  db-migrate.yaml        # 手動 dispatch: ビルド → push → Cloud Run Job 実行
  db-migrate-on-push.yaml  # 自動: common の push で dev に適用
```

## 関連リポジトリ

| リポジトリ | 内容 |
|-----------|------|
| [overload-party-common](https://github.com/kenyamaneko/overload-party-common) | 共有データ（スキーマ SQL, カード定義, ドキュメント） |
| [overload-party-infra](https://github.com/kenyamaneko/overload-party-infra) | Terraform（Cloud Run Job / Cloud SQL / IAM） |
