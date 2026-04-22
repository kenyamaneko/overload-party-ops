# overload-party-ops

Overload Party の運用ジョブ・サービスをまとめたリポジトリ。GitHub Actions の定時実行・CD パイプライン・Slack からの運用操作を集約する。

各ジョブ・サービスのセットアップや設定は個別の README を参照。

## 定時実行ジョブ（GitHub Actions schedule）

| ジョブ | 説明 | スケジュール |
|---|---|---|
| [nightly-shutdown](nightly-shutdown/README.md) | dev 環境のリソース停止（コスト削減） | 毎日 2:00 JST (`0 17 * * *` UTC) |
| [nightly-review](nightly-review/README.md) | 全リポジトリの自動レビュー → GitHub Issues 起票 | 毎日 3:00 JST (`0 18 * * *` UTC) |
| [drift-monitor](drift-monitor/README.md) | Terraform plan による drift 検出 → Slack 通知 | 毎日 7:00 JST (`0 22 * * *` UTC) |
| [cost-monitor](cost-monitor/README.md) | dev/stg のコスト発生リソース検出 → Slack 通知 | 毎日 8:00 / 14:00 / 20:00 JST (`0 23,5,11 * * *` UTC) |

## CD パイプライン

| 名前 | 説明 | トリガー |
|---|---|---|
| [db-migrate](db-migrate/README.md) | Cloud SQL スキーママイグレーション（Cloud Run Job） | service repo main push（dev 自動）/ 手動 dispatch |
| [slack-commands](slack-commands/README.md) | Slack スラッシュコマンド本体（Cloud Run Service） | main push / 手動 dispatch |
| [slack-commands-worker](slack-commands-worker/README.md) | Slack 署名検証 + 即時応答（Cloudflare Worker） | main push / 手動 dispatch |

## ローカル開発

Cloud Run 系サービスは Makefile でビルド・デプロイする。

```bash
make build-db-migrate               # db-migrate イメージのビルド
make deploy-db-migrate              # ビルド + AR push + Cloud Run Job 更新
make deploy-service-slack-commands  # slack-commands Cloud Run Service 更新
make help                           # コマンド一覧
```
