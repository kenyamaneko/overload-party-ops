# cross-repo-seeds

shop / card 両 repo の seed YAML 間の参照整合性を検証し、結果を Slack に通知する。

## チェック対象

| 観点 | 検出条件 |
|---|---|
| `shop.card_pack_id ⊂ card.pack_id` | overload-party-shop `data/products.yaml` の `products[*].card_pack_id` 集合が overload-party-card `data/card_packs.yaml` の `packs[*].pack_id` 集合に含まれること |

不整合があれば「どの shop product がどの card_pack_id を参照していて card 側に存在しないか」を Slack に報告し non-zero exit する。

## トリガー

- cron: 18:00 UTC = 03:00 JST 翌日 (daily)
- workflow_dispatch (手動)
- pull_request (Markdown / docs 等ドキュメントのみの変更を除く)

## Slack 通知

検証結果を成功・失敗いずれも Slack に通知する。

- 成功時: `:white_check_mark: *[参照整合 {JST日付}] card_pack OK*` (cost-monitor / drift-monitor と同じ日付ヘッダ書式)
- 失敗時: `:x: *[参照整合 {JST日付}] card_pack 不整合*` + 各 product → 未定義 pack_id の一覧 + Actions ログ URL

`SLACK_WEBHOOK_URL` は必須。未設定なら通知経路が無い異常として exit 1 で落とす (cost-monitor / drift-monitor と同仕様)。Webhook 送信と Actions run URL 組み立ては `slack_notifier.py` に切り出してあり、card_pack 以外の cross-repo 検証を追加する際も再利用できる。

## 必要な Secret / Variable

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `SLACK_WEBHOOK_URL` | Slack 通知用 Webhook URL |
| Variable | `CROSS_REPO_DEPS_APP_ID` | shop / card private repo を fetch する Cross-Repo Deps App ID |
| Secret | `CROSS_REPO_DEPS_APP_PRIVATE_KEY` | 同 App の private key (PEM) |
