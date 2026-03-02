# overload-party-ops

Overload Party の運用ジョブ管理リポジトリ。Cloud Run Jobs で実行するコンテナイメージと GitHub Actions ワークフローを管理する。

## ジョブ一覧

| ジョブ | 説明 | ツール |
|--------|------|--------|
| `db-migrate` | Cloud SQL スキーママイグレーション + IAM 権限付与 | psqldef, psql |

## 使い方

### DB マイグレーション

GitHub Actions の `workflow_dispatch` から手動実行する。

1. **Actions** → **DB Migration** → **Run workflow**
2. `environment`: `dev` or `stg`
3. `server_ref`: overload-party-server のブランチ/タグ/SHA（デフォルト: `main`）
4. `dry_run`: `true` にするとイメージ更新のみ（ジョブ実行しない）

### 仕組み

```
overload-party-server (db/)      overload-party-infra (Terraform)
        │                                 │
        │ SQL files                       │ Cloud Run Job リソース定義
        ▼                                 │ SA / IAM / Secret Manager
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
  db-migrate.yaml        # ビルド → push → Cloud Run Job 実行
```

## 関連リポジトリ

| リポジトリ | 内容 |
|-----------|------|
| [overload-party-server](https://github.com/kenyamaneko/overload-party-server) | Go ゲームサーバー（マイグレーション SQL を保持） |
| [overload-party-infra](https://github.com/kenyamaneko/overload-party-infra) | Terraform（Cloud Run Job / Cloud SQL / IAM） |
