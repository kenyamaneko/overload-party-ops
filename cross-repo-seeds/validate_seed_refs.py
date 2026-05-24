#!/usr/bin/env python3
"""shop / card 両 repo の seed YAML 間の参照整合性を検証し Slack 通知する。

shop の data/products.yaml に定義された product の card_pack_id が、card の
data/card_packs.yaml に定義された pack_id 集合に含まれることを確認する。
不整合があれば失敗 (どの shop product がどの card_pack_id を参照していて card 側に
無いか) を Slack に通知して non-zero exit する。

Usage:
    SLACK_WEBHOOK_URL=https://hooks.slack.com/... \\
    python3 cross-repo-seeds/check.py \\
        --shop-yaml /path/to/overload-party-shop/data/products.yaml \\
        --card-yaml /path/to/overload-party-card/data/card_packs.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

SLACK_TEXT_LIMIT = 40000


def load_shop_card_pack_refs(shop_yaml: Path) -> dict[str, str]:
    """shop products.yaml から product_id -> card_pack_id の dict を返す.

    card_pack_id を持つ product type (faction_set / card_pack) のみ抽出。
    cosmetic / subscription 等は card_pack 参照を持たないのでスキップ。
    """
    data = yaml.safe_load(shop_yaml.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "products" not in data:
        raise ValueError(f"{shop_yaml}: top-level 'products' key is required")
    refs: dict[str, str] = {}
    for p in data["products"] or []:
        if "card_pack_id" in p:
            refs[p["product_id"]] = p["card_pack_id"]
    return refs


def load_card_pack_ids(card_yaml: Path) -> set[str]:
    """card card_packs.yaml から pack_id 集合を返す."""
    data = yaml.safe_load(card_yaml.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "packs" not in data:
        raise ValueError(f"{card_yaml}: top-level 'packs' key is required")
    return {p["pack_id"] for p in data["packs"] or []}


def find_missing(shop_refs: dict[str, str], card_pack_ids: set[str]) -> list[tuple[str, str]]:
    """shop の card_pack_id 参照のうち card 側に存在しないものを (product_id, card_pack_id) で返す."""
    return sorted(
        (product_id, pack_id)
        for product_id, pack_id in shop_refs.items()
        if pack_id not in card_pack_ids
    )


def notify_slack(webhook_url: str, message: str) -> None:
    """Slack Webhook にメッセージを送信します。"""
    if len(message) > SLACK_TEXT_LIMIT:
        message = message[:SLACK_TEXT_LIMIT] + "\n…(truncated)"
    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"Slack notification failed: {e}", file=sys.stderr)
        sys.exit(1)


def _actions_run_url() -> str:
    """GitHub Actions 実行中なら当該 run の URL を返します。ローカル実行時は空文字。"""
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def _format_failure_message(missing: list[tuple[str, str]], run_url: str) -> str:
    """Slack 失敗通知の本文を組み立てる."""
    lines = [
        f":x: *[seed 整合検証 失敗]* shop の card_pack_id 参照 {len(missing)} 件が card 側に存在しません。",
    ]
    if run_url:
        lines.append(f"<{run_url}|GitHub Actions ログ>")
    lines.append("")
    for product_id, pack_id in missing:
        lines.append(f"  • shop 商品 `{product_id}` → card_pack_id `{pack_id}` (card に未定義)")
    return "\n".join(lines)


def main() -> int:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shop-yaml", type=Path, required=True, help="overload-party-shop/data/products.yaml")
    parser.add_argument("--card-yaml", type=Path, required=True, help="overload-party-card/data/card_packs.yaml")
    args = parser.parse_args()

    shop_refs = load_shop_card_pack_refs(args.shop_yaml)
    card_pack_ids = load_card_pack_ids(args.card_yaml)
    missing = find_missing(shop_refs, card_pack_ids)

    if not missing:
        print(
            f"OK: {len(shop_refs)} shop product(s) all reference valid card_pack_id "
            f"(card defines {len(card_pack_ids)} pack(s))"
        )
        return 0

    sys.stderr.write(
        f"error: {len(missing)} shop product(s) reference unknown card_pack_id "
        f"(not in {args.card_yaml.name}):\n"
    )
    for product_id, pack_id in missing:
        sys.stderr.write(f"  - shop product {product_id!r} → card_pack_id {pack_id!r}\n")

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if webhook_url:
        notify_slack(webhook_url, _format_failure_message(missing, _actions_run_url()))
    else:
        print("SLACK_WEBHOOK_URL is not set, skipping Slack notification", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
