"""cross-repo-seeds/validate_card_pack_refs.py のユニットテスト."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import validate_card_pack_refs as v


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


class TestLoadShopRefs:
    """shop products.yaml からの card_pack_id 抽出仕様."""

    def test_extracts_card_pack_id_from_faction_set_and_card_pack(self, tmp_path: Path):
        path = tmp_path / "products.yaml"
        _write_yaml(path, {
            "products": [
                {"product_id": "fs_she", "type": "faction_set", "card_pack_id": "faction_set_she"},
                {"product_id": "pack_x", "type": "card_pack", "card_pack_id": "limited_x"},
            ],
        })
        assert v.load_shop_card_pack_refs(path) == {
            "fs_she": "faction_set_she",
            "pack_x": "limited_x",
        }

    def test_skips_products_without_card_pack_id(self, tmp_path: Path):
        """card_pack_id を持たない product (cosmetic / subscription 想定) はスキップする."""
        path = tmp_path / "products.yaml"
        _write_yaml(path, {
            "products": [
                {"product_id": "fs_she", "type": "faction_set", "card_pack_id": "faction_set_she"},
                {"product_id": "stamp_a", "type": "cosmetic"},
            ],
        })
        assert v.load_shop_card_pack_refs(path) == {"fs_she": "faction_set_she"}

    def test_missing_top_level_products_raises(self, tmp_path: Path):
        path = tmp_path / "products.yaml"
        _write_yaml(path, {"other_key": []})
        with pytest.raises(ValueError, match="top-level 'products' key is required"):
            v.load_shop_card_pack_refs(path)


class TestLoadCardPackIds:
    def test_extracts_all_pack_ids(self, tmp_path: Path):
        path = tmp_path / "card_packs.yaml"
        _write_yaml(path, {
            "packs": [
                {"pack_id": "basic", "cards": []},
                {"pack_id": "faction_set_she", "cards": []},
            ],
        })
        assert v.load_card_pack_ids(path) == {"basic", "faction_set_she"}

    def test_missing_top_level_packs_raises(self, tmp_path: Path):
        path = tmp_path / "card_packs.yaml"
        _write_yaml(path, {"other_key": []})
        with pytest.raises(ValueError, match="top-level 'packs' key is required"):
            v.load_card_pack_ids(path)


class TestFindMissing:
    def test_all_refs_present_returns_empty_list(self):
        shop_refs = {"fs_she": "faction_set_she"}
        card_ids = {"basic", "faction_set_she"}
        assert v.find_missing(shop_refs, card_ids) == []

    def test_missing_pack_id_reported_with_product_id(self):
        shop_refs = {"fs_ghost": "faction_set_ghost"}
        card_ids = {"basic"}
        assert v.find_missing(shop_refs, card_ids) == [("fs_ghost", "faction_set_ghost")]

    def test_multiple_missing_reported_sorted(self):
        shop_refs = {"z": "z_pack", "a": "a_pack", "ok": "basic"}
        card_ids = {"basic"}
        assert v.find_missing(shop_refs, card_ids) == [("a", "a_pack"), ("z", "z_pack")]


class TestFormatFailureMessage:
    """Slack 失敗通知の整形仕様."""

    def test_includes_missing_product_and_pack_ids(self):
        msg = v._format_failure_message(
            [("fs_ghost", "faction_set_ghost"), ("fs_lost", "limited_lost")],
            run_url="",
        )
        assert "2 件" in msg
        assert ":x:" in msg
        assert "fs_ghost" in msg
        assert "faction_set_ghost" in msg
        assert "fs_lost" in msg
        assert "limited_lost" in msg

    def test_includes_actions_run_url_when_available(self):
        msg = v._format_failure_message([("p", "q")], run_url="https://github.com/org/repo/actions/runs/1")
        assert "<https://github.com/org/repo/actions/runs/1|GitHub Actions ログ>" in msg

    def test_omits_url_block_when_run_url_empty(self):
        msg = v._format_failure_message([("p", "q")], run_url="")
        # 空 run_url 時は URL line を出さない (ローカル実行を仮定)
        assert "GitHub Actions ログ" not in msg


class TestFormatSuccessMessage:
    """Slack 成功通知の整形仕様."""

    def test_conveys_only_that_check_passed(self):
        """観点: 成功通知は通過したことだけを示す短文にする (件数や URL は載せない)."""
        msg = v._format_success_message()
        assert ":white_check_mark:" in msg
        assert "card_pack 参照整合 OK" in msg


class TestMainIntegration:
    """CLI 終了コードと Slack 通知の呼び出し."""

    def _write_pair(self, tmp_path: Path, shop_products, card_packs):
        shop = tmp_path / "products.yaml"
        card = tmp_path / "card_packs.yaml"
        _write_yaml(shop, {"products": shop_products})
        _write_yaml(card, {"packs": card_packs})
        return shop, card

    def test_valid_refs_notifies_success(self, tmp_path: Path, capsys, monkeypatch):
        """観点: 全参照が有効なら exit 0 で成功メッセージを Slack に送る."""
        shop, card = self._write_pair(
            tmp_path,
            [{"product_id": "fs_she", "type": "faction_set", "card_pack_id": "faction_set_she"}],
            [{"pack_id": "faction_set_she", "cards": []}],
        )
        monkeypatch.setattr("sys.argv", ["c", "--shop-yaml", str(shop), "--card-yaml", str(card)])
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/x"}, clear=True), \
             patch("slack_notifier.post_to_slack") as slack:
            assert v.main() == 0
        slack.assert_called_once()
        webhook, msg = slack.call_args[0]
        assert webhook == "https://hooks.slack.com/x"
        assert "card_pack 参照整合 OK" in msg
        assert "OK" in capsys.readouterr().out

    def test_missing_refs_notifies_failure(self, tmp_path: Path, capsys, monkeypatch):
        """観点: 不整合があれば exit 1 で失敗詳細を Slack に送る."""
        shop, card = self._write_pair(
            tmp_path,
            [{"product_id": "fs_ghost", "type": "faction_set", "card_pack_id": "faction_set_ghost"}],
            [{"pack_id": "basic", "cards": []}],
        )
        monkeypatch.setattr("sys.argv", ["c", "--shop-yaml", str(shop), "--card-yaml", str(card)])
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/x"}, clear=True), \
             patch("slack_notifier.post_to_slack") as slack:
            assert v.main() == 1
        slack.assert_called_once()
        webhook, msg = slack.call_args[0]
        assert webhook == "https://hooks.slack.com/x"
        assert "fs_ghost" in msg
        assert "faction_set_ghost" in msg

    def test_missing_webhook_exits_with_error(self, tmp_path: Path, capsys, monkeypatch):
        """観点: SLACK_WEBHOOK_URL 未設定は通知経路が無い異常として exit 1 で落とす (他 Slack ジョブと同仕様)."""
        shop, card = self._write_pair(
            tmp_path,
            [{"product_id": "fs_she", "type": "faction_set", "card_pack_id": "faction_set_she"}],
            [{"pack_id": "faction_set_she", "cards": []}],
        )
        monkeypatch.setattr("sys.argv", ["c", "--shop-yaml", str(shop), "--card-yaml", str(card)])
        with patch.dict(os.environ, {}, clear=True), patch("slack_notifier.post_to_slack") as slack:
            with pytest.raises(SystemExit) as exc:
                v.main()
        assert exc.value.code == 1
        slack.assert_not_called()
        assert "SLACK_WEBHOOK_URL is not set" in capsys.readouterr().err
