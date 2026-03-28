# DB Migrate

psqldef ベースのスキーママイグレーションシステム。overload-party-common リポジトリの SQL 定義を Cloud Run Job 経由で Cloud SQL に適用する。

## 仕組み

1. common リポから SQL ファイル（`schema_postgres.sql`, `grant_iam.sql`）を sparse-checkout で取得
2. psqldef を含む Docker イメージをビルドし Artifact Registry にプッシュ
3. Cloud Run Job のイメージを更新
4. Cloud Run Job を実行し、Cloud SQL にスキーマを適用 + IAM 権限を付与

psqldef は宣言的スキーマ管理ツールで、現在の DB 状態と SQL 定義の差分を自動で計算・適用する。

> psqldef の upstream バグ（dropped column で NULL スキャン）に対するパッチを Dockerfile 内で適用している。

## スキーマ安全チェック

`schema_check.py` が新旧スキーマを比較し、破壊的変更を検出する。

- `DROP TABLE` の検出
- `DROP COLUMN` の検出

破壊的変更が検出された場合、ワークフローは失敗する。意図的な変更の場合は dry-run で差分を確認した上で対応する。

## トリガー

### repository_dispatch（自動）

common リポの main push 時に `db-migrate` イベントが発火し、**dev 環境のみ**自動実行される。

### workflow_dispatch（手動）

GitHub Actions の UI から手動実行。以下のパラメータを指定できる:

| パラメータ | 説明 | デフォルト |
|-----------|------|-----------|
| `environment` | 対象環境（`dev` / `stg`） | `dev` |
| `dry_run` | dry-run モード（イメージ更新のみ、ジョブ実行なし） | `false` |

## Dry-run モード

`dry_run=true` で実行すると、Docker イメージのビルド・プッシュと Cloud Run Job のイメージ更新までは行うが、**ジョブの実行はスキップ**される。スキーマ変更の安全性を事前確認したい場合に使う。

## セットアップ

### GitHub Secrets

ops リポジトリの Settings > Secrets and variables > Actions > Secrets:

| 名前 | 値 |
|------|-----|
| `DB_MIGRATE_TOKEN` | common リポへのアクセス用 PAT |

### GitHub Variables（環境ごと）

ops リポジトリの Settings > Environments > `dev` / `stg` > Environment variables:

| 名前 | 値 |
|------|-----|
| `WIF_PROVIDER` | Workload Identity Federation プロバイダ |
| `CI_SERVICE_ACCOUNT` | CI 用サービスアカウント |
| `CLOUDSQL_INSTANCE_NAME` | Cloud SQL インスタンス名 |
