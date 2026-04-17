# Nightly Shutdown

dev 環境のリソースを毎晩自動停止し、コストを削減するスケジューラ。

## 実装方針

ADR「ノードプールスケーリング戦略とGKEの所有権」の所有権原則に従い、
本ワークフローは **実処理を各リソース所有リポに委譲する薄いスケジューラ** として動作する。

実処理の所在:

| リソース | 担当リポ | ワークフロー |
|---|---|---|
| GKE Ingress / DNS / PSC / 後続 node pool resize | `overload-party-k8s` | `env-lifecycle.yaml` (action=down) |
| Cloud SQL activation policy | `overload-party-infra` | `cloudsql-activation.yaml` (action=down) |
| GKE node pool resize | `keyandnotes-platform` | `node-pool-scale.yaml` (env-lifecycle から連鎖) |

定期実行に加え、`workflow_dispatch` から dev / stg を選択して手動実行も可能。

## 起動 (morning wake-up) について

朝の自動起動スケジュールは設けない。起動が必要なときは以下を手動ディスパッチする:

- `overload-party-k8s/env-lifecycle.yaml` (action=up)
- `overload-party-infra/cloudsql-activation.yaml` (action=up)

Slack コマンド `/gke-up <env>` / `/db-start <env>` でも実行可能。

## 必要なシークレット / 変数

本ワークフローは他リポへの `workflow_dispatch` のみ行う:

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `K8S_DISPATCH_TOKEN` | `overload-party-k8s` に対する Actions: write 権限の fine-grained PAT |
| Secret | `INFRA_DISPATCH_TOKEN` | `overload-party-infra` に対する Actions: write 権限の fine-grained PAT |
