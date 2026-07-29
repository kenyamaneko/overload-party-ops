# Nightly Shutdown

dev / stg 環境のリソースを毎晩自動停止し、コストを削減するスケジューラ。

## 実装方針

本ワークフローは **実処理をリソース所有リポに委譲する薄いスケジューラ** として動作する。

実処理の所在:

| リソース | 担当リポ | ワークフロー |
|---|---|---|
| Cloud SQL activation policy | `overload-party-infra` | `cloudsql-activation.yaml` (action=down) |

Cloud Run は使わないあいだインスタンス数が 0 になり課金されないため、停止操作の対象は Cloud SQL だけになる。

定期実行に加え、`workflow_dispatch` から dev / stg を選択して手動実行も可能。

## 起動 (morning wake-up) について

朝の自動起動スケジュールは設けない。起動が必要なときは `overload-party-infra/cloudsql-activation.yaml` (action=up) を手動ディスパッチする。

## 必要なシークレット / 変数

本ワークフローは他リポへの `workflow_dispatch` のみ行う:

| 種別 | 名前 | 用途 |
|---|---|---|
| Variable | `OPS_AUTOMATION_APP_ID` | dispatch 用 GitHub App (Ops Automation) の App ID |
| Secret | `OPS_AUTOMATION_APP_PRIVATE_KEY` | 同 App の秘密鍵。短命 token を発行し dispatch に使用 |
