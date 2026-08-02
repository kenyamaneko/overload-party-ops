#!/usr/bin/env python3
"""shop / card 両 repo の seed YAML 間の card_pack 参照整合性を検証し Slack 通知する。

shop の data/products.yaml に定義された product の card_pack_id が、card の
data/card_packs.yaml に定義された pack_id 集合に含まれることを確認する。検証結果は
成功・失敗いずれも Slack に通知し、不整合があれば non-zero exit する。

Usage:
    SLACK_WEBHOOK_URL=https://hooks.slack.com/... \\
    python3 cross-repo-seeds/validate_card_pack_refs.py \\
        --shop-yaml /path/to/overload-party-shop/data/products.yaml \\
        --card-yaml /path/to/overload-party-card/data/card_packs.yaml
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import slack_notifier

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

_JST_OFFSET_HOURS = 9

# 未知の type を「card_pack 参照を持たない」と推定すると検証対象から静かに外れるため、
# どちらの集合にも属さない type はエラーにする。
_CARD_PACK_REQUIRED_TYPES = frozenset({"faction_set", "card_pack"})
_CARD_PACK_UNRELATED_TYPES = frozenset({"cosmetic", "subscription"})


def _require_non_empty_list(data: dict, key: str, source: Path) -> list:
    """YAML のトップレベルキーが空でないリストであることを確認して返す.

    Raises:
        ValueError: キーが存在しない、リストでない、または空の場合。
    """
    if not isinstance(data, dict) or key not in data:
        raise ValueError(f"{source}: top-level '{key}' key is required")
    value = data[key]
    if not isinstance(value, list):
        raise ValueError(f"{source}: '{key}' must be a list")
    if not value:
        raise ValueError(f"{source}: '{key}' must not be empty")
    return value


def load_shop_card_pack_refs(shop_yaml: Path) -> dict[str, str]:
    """shop products.yaml から product_id -> card_pack_id の dict を返す.

    card_pack を参照する type (faction_set / card_pack) を対象とし、参照を持たない
    type (cosmetic / subscription) は対象外とする。

    Raises:
        ValueError: products が空、product が dict でない、product_id が無い、
            type が未知、または対象 type が card_pack_id を欠く場合。
    """
    data = yaml.safe_load(shop_yaml.read_text(encoding="utf-8"))
    products = _require_non_empty_list(data, "products", shop_yaml)

    refs: dict[str, str] = {}
    for p in products:
        if not isinstance(p, dict):
            raise ValueError(f"{shop_yaml}: every product must be a mapping, got {p!r}")
        product_id = p.get("product_id")
        if not product_id:
            raise ValueError(f"{shop_yaml}: every product requires 'product_id'")
        product_type = p.get("type")
        if product_type in _CARD_PACK_UNRELATED_TYPES:
            continue
        if product_type not in _CARD_PACK_REQUIRED_TYPES:
            raise ValueError(
                f"{shop_yaml}: product '{product_id}' has unknown type {product_type!r}; "
                f"cannot tell whether it must reference a card_pack"
            )
        if "card_pack_id" not in p:
            raise ValueError(
                f"{shop_yaml}: product '{product_id}' of type '{product_type}' "
                f"requires 'card_pack_id'"
            )
        refs[product_id] = p["card_pack_id"]
    return refs


def load_card_pack_ids(card_yaml: Path) -> set[str]:
    """card card_packs.yaml から pack_id 集合を返す.

    Raises:
        ValueError: packs が空、pack が dict でない、または pack_id が無い場合。
    """
    data = yaml.safe_load(card_yaml.read_text(encoding="utf-8"))
    packs = _require_non_empty_list(data, "packs", card_yaml)

    pack_ids: set[str] = set()
    for p in packs:
        if not isinstance(p, dict):
            raise ValueError(f"{card_yaml}: every pack must be a mapping, got {p!r}")
        pack_id = p.get("pack_id")
        if not pack_id:
            raise ValueError(f"{card_yaml}: every pack requires 'pack_id'")
        pack_ids.add(pack_id)
    return pack_ids


def find_missing(shop_refs: dict[str, str], card_pack_ids: set[str]) -> list[tuple[str, str]]:
    """shop の card_pack_id 参照のうち card 側に存在しないものを (product_id, card_pack_id) で返す."""
    return sorted(
        (product_id, pack_id)
        for product_id, pack_id in shop_refs.items()
        if pack_id not in card_pack_ids
    )


def _format_failure_message(missing: list[tuple[str, str]], today: str, run_url: str) -> str:
    """Slack 失敗通知の本文を組み立てる.

    Args:
        missing: card 側に存在しない (product_id, card_pack_id) の一覧。
        today: 通知日付 (JST, YYYY-MM-DD)。
        run_url: GitHub Actions run の URL。空なら URL 行を省く。

    Returns:
        Slack に送る失敗通知の本文。
    """
    lines = [
        f":x: *[参照整合 {today}] card_pack 不整合* "
        f"shop の card_pack_id 参照 {len(missing)} 件が card 側に存在しません。",
    ]
    if run_url:
        lines.append(f"<{run_url}|GitHub Actions ログ>")
    lines.append("")
    for product_id, pack_id in missing:
        lines.append(f"  • shop 商品 `{product_id}` → card_pack_id `{pack_id}` (card に未定義)")
    return "\n".join(lines)


def _format_success_message(today: str) -> str:
    """Slack 成功通知の本文を組み立てる.

    Args:
        today: 通知日付 (JST, YYYY-MM-DD)。

    Returns:
        Slack に送る成功通知の本文。
    """
    return f":white_check_mark: *[参照整合 {today}] card_pack OK*"


def main() -> int:
    """エントリポイント。

    Returns:
        プロセス終了コード (0=参照整合, 1=不整合検出)。
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shop-yaml", type=Path, required=True, help="overload-party-shop/data/products.yaml")
    parser.add_argument("--card-yaml", type=Path, required=True, help="overload-party-card/data/card_packs.yaml")
    args = parser.parse_args()

    webhook_url = slack_notifier.require_webhook_url()
    jst = timezone(timedelta(hours=_JST_OFFSET_HOURS))
    today = datetime.now(jst).strftime("%Y-%m-%d")

    shop_refs = load_shop_card_pack_refs(args.shop_yaml)
    card_pack_ids = load_card_pack_ids(args.card_yaml)
    missing = find_missing(shop_refs, card_pack_ids)

    if not missing:
        print(
            f"OK: {len(shop_refs)} shop product(s) all reference valid card_pack_id "
            f"(card defines {len(card_pack_ids)} pack(s))"
        )
        slack_notifier.post_to_slack(webhook_url, _format_success_message(today))
        return 0

    sys.stderr.write(
        f"error: {len(missing)} shop product(s) reference unknown card_pack_id "
        f"(not in {args.card_yaml.name}):\n"
    )
    for product_id, pack_id in missing:
        sys.stderr.write(f"  - shop product {product_id!r} → card_pack_id {pack_id!r}\n")

    run_url = slack_notifier.build_actions_run_url()
    slack_notifier.post_to_slack(webhook_url, _format_failure_message(missing, today, run_url))
    return 1


if __name__ == "__main__":
    sys.exit(main())
