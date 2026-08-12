# overload-party-ops

カードゲーム Overload Party の運用ジョブ・CD パイプラインを担うリポジトリ。

各ジョブ・サービスのセットアップや設定は個別の README を参照。

## 技術スタック

| レイヤー | 技術 |
|---|---|
| 言語 | Python, シェルスクリプト |
| DBマイグレーション | sqldef |
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
| [db-migrate](db-migrate/README.md) | Cloud SQL スキーママイグレーション | service repo main push（dev 自動）/ 手動 dispatch |

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [セットアップ](docs/SETUP.md) | ローカル開発のビルド・デプロイコマンド |
| [ADR](https://github.com/kenyamaneko/overload-party-common/tree/main/docs/adr)（commonリポジトリ） | 設計判断の背景・理由・結果 |
| [システム構成図](https://github.com/kenyamaneko/overload-party-common#システム構成図)（commonリポジトリ） | Overload Party 全体の構成図 |
