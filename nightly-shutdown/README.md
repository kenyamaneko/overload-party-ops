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

## スケジュール

- **定期実行**: 毎日 2:00 AM JST（dev 環境のみ）
- **手動実行**: `workflow_dispatch` から dev / stg を選択して実行可能

## 起動 (morning wake-up) について

朝の自動起動スケジュールは設けない。起動が必要なときは人間が以下を手動ディスパッチする:

- `overload-party-k8s/env-lifecycle.yaml` (action=up)
- `overload-party-infra/cloudsql-activation.yaml` (action=up)

Slack コマンド `/gke-up <env>` / `/sql-up <env>` 等を使えば 1 クリックで実行可能
(実装状況は各リポ参照)。

## セットアップ

本ワークフローは **他リポへの workflow_dispatch** のみ行うため、
以下の GitHub secrets が必要:

- `K8S_DISPATCH_TOKEN`: `overload-party-k8s` に対する Actions: write 権限の fine-grained PAT
- `INFRA_DISPATCH_TOKEN`: `overload-party-infra` に対する Actions: write 権限の fine-grained PAT
