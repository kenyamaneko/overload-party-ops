# overload-party-ops

Overload Party の運用ジョブ・サービスをまとめたリポジトリ。GitHub Actions の定時実行と CD パイプラインを集約する。

各ジョブ・サービスのセットアップや設定は個別の README を参照。

## 技術スタック

| レイヤー | 技術 |
|---|---|
| 言語 | Python (定時ジョブ), Bash (db-migrate 実行時。psqldef を使用) |
| 実行基盤 | GitHub Actions (定時実行) / Cloud Run Job (db-migrate) |
| 通知 | Slack |

## 定時実行ジョブ（GitHub Actions schedule）

| ジョブ | 説明 | スケジュール |
|---|---|---|
| [nightly-shutdown](nightly-shutdown/README.md) | dev / stg 環境のリソース停止（コスト削減） | 毎日 2:00 JST (`0 17 * * *` UTC) |
| [cross-repo-seeds](cross-repo-seeds/README.md) | shop / card seed YAML の参照整合検証 → Slack 通知 | 毎日 3:00 JST (`0 18 * * *` UTC) |
| [drift-monitor](drift-monitor/README.md) | Terraform plan による drift 検出 → Slack 通知 | 毎日 7:00 JST (`0 22 * * *` UTC) |
| [cost-monitor](cost-monitor/README.md) | dev/stg のコスト発生リソース検出 → Slack 通知 | 毎日 8:00 / 14:00 / 20:00 JST (`0 23,5,11 * * *` UTC) |

## CD パイプライン

| 名前 | 説明 | トリガー |
|---|---|---|
| [db-migrate](db-migrate/README.md) | Cloud SQL スキーママイグレーション（Cloud Run Job） | service repo main push（dev 自動）/ 手動 dispatch |

## ローカル開発

Cloud Run 系サービスは Makefile でビルド・デプロイする。

```bash
make build-db-migrate               # db-migrate イメージのビルド
make deploy-db-migrate              # ビルド + AR push + Cloud Run Job 更新
make help                           # コマンド一覧
```
