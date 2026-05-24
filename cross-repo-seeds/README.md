# cross-repo-seeds

shop / card 両 repo の seed YAML 間の参照整合性を検証し、不整合があれば Slack に通知する。

## チェック対象

| 観点 | 検出条件 |
|---|---|
| `shop.card_pack_id ⊂ card.pack_id` | overload-party-shop `data/products.yaml` の `products[*].card_pack_id` 集合が overload-party-card `data/card_packs.yaml` の `packs[*].pack_id` 集合に含まれること |

不整合があれば「どの shop product がどの card_pack_id を参照していて card 側に存在しないか」を Slack に報告し non-zero exit する。

## トリガー

- cron: 18:00 UTC = 03:00 JST 翌日 (daily)
- workflow_dispatch (手動)
- pull_request (validate_card_pack_refs.py / test_validate_card_pack_refs.py / workflow 変更時)

## Slack 通知

- 失敗時のみ: `:x: [card_pack 参照整合 失敗]` + 各 product → 未定義 pack_id の一覧 + Actions ログ URL
- 成功時は Slack 通知なし (cron 健全性は GitHub Actions の history で確認)

## 必要な Secret / Variable

| 種別 | 名前 | 用途 |
|---|---|---|
| Secret | `SLACK_WEBHOOK_URL` | Slack 通知用 Webhook URL |
| Variable | `CROSS_REPO_DEPS_APP_ID` | shop / card private repo を fetch する Cross-Repo Deps App ID |
| Secret | `CROSS_REPO_DEPS_APP_PRIVATE_KEY` | 同 App の private key (PEM) |
