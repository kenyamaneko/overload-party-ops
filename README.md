# overload-party-ops

Overload Party の運用ジョブ・サービス管理リポジトリ。

## ジョブ・サービス

### 定時実行ジョブ（GitHub Actions schedule）

| ジョブ | 説明 | スケジュール |
|--------|------|------------|
| [`nightly-review`](nightly-review/) | 全リポジトリの自動レビュー → GitHub Issues 起票 | 毎日 3:00 JST |
| [`nightly-shutdown`](nightly-shutdown/) | dev 環境のリソース停止（コスト削減） | 毎日 2:00 JST |
| [`cost-monitor`](cost-monitor/) | dev/stg のコスト発生リソース検出 → Slack 通知 | 毎日 8:00 JST |
| [`drift-monitor`](drift-monitor/) | Terraform plan による drift 検出 → Slack 通知 | 毎日 7:00 JST |

### CD パイプライン

| 名前 | 説明 | トリガー |
|------|------|---------|
| [`db-migrate`](db-migrate/) | Cloud SQL スキーママイグレーション | common push（dev 自動）/ 手動 dispatch |
| [`slack-commands`](slack-commands/) | Slack スラッシュコマンドサービス | main push / 手動 dispatch |
| `slack-commands-worker` | Cloudflare Worker（Slack 署名検証 + 即時応答） | main push / 手動 dispatch |

各ジョブ・サービスのセットアップや設定は個別の README を参照。

## ディレクトリ構成

```
db-migrate/              # DB マイグレーション（Cloud Run Job）
nightly-review/          # 夜間自動レビュー
nightly-shutdown/        # 夜間リソース停止
cost-monitor/            # 環境コスト監視
drift-monitor/           # Terraform drift 検出
slack-commands/          # Slack スラッシュコマンド（Cloud Run Service）
slack-commands-worker/   # Cloudflare Worker
terraform/
  shared/                # deploy SA の IAM + 共有 Secret
  slack_commands/        # Cloud Run Service + SA + IAM
.github/workflows/       # GitHub Actions ワークフロー
Makefile                 # ローカル開発用コマンド
```

## ローカル開発

```bash
# DB マイグレーションイメージのビルド
make build-db-migrate

# ビルド + AR push + Cloud Run Job 更新
make deploy-db-migrate

# Cloud Run Service 更新
make deploy-service-slack-commands

# コマンド一覧
make help
```

## ロールバック

psqldef は宣言的な DDL 適用ツールであり、自動ロールバック機能はない。詳細は [db-migrate/README.md](db-migrate/README.md) を参照。

### 前バージョンへのスキーマロールバック

1. 前のコミットハッシュで `db-migrate.yaml` を `workflow_dispatch` 実行
2. `dry_run: true` で事前確認してから `dry_run: false` で適用

### データのロールバック（Cloud SQL バックアップから復元）

```bash
gcloud sql backups list --instance=overload-party-db --project=<PROJECT>
gcloud sql backups restore <BACKUP_ID> --restore-instance=overload-party-db --project=<PROJECT>
```

### 緊急時の直接修正（Cloud SQL Auth Proxy 経由）

```bash
cloud-sql-proxy <PROJECT>:<REGION>:overload-party-db --port=5432
psql -h 127.0.0.1 -U <USER> -d overload_party
```

> **注意:** 直接修正は psqldef の管理状態と乖離するため、修正後に `schema_postgres.sql` を必ず同期すること。

## 関連リポジトリ

| リポジトリ | 内容 |
|-----------|------|
| [overload-party-common](https://github.com/kenyamaneko/overload-party-common) | 共有データ（スキーマ SQL, カード定義, ドキュメント） |
| [overload-party-infra](https://github.com/kenyamaneko/overload-party-infra) | Terraform（Cloud Run Job / Cloud SQL / IAM） |
