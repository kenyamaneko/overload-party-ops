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


class TestShopのcard_pack_id抽出:
    def test_faction_setとcard_packからcard_pack_idを抽出する(self, tmp_path: Path):
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

    @pytest.mark.parametrize(
        "product_type",
        [
            pytest.param("cosmetic", id="cosmetic は card_pack を参照しないので対象外になる"),
            pytest.param("subscription", id="subscription は card_pack を参照しないので対象外になる"),
        ],
    )
    def test_card_packを参照しないtypeは抽出対象から外れる(self, tmp_path: Path, product_type):
        path = tmp_path / "products.yaml"
        _write_yaml(path, {
            "products": [
                {"product_id": "fs_she", "type": "faction_set", "card_pack_id": "faction_set_she"},
                {"product_id": "other", "type": product_type},
            ],
        })
        assert v.load_shop_card_pack_refs(path) == {"fs_she": "faction_set_she"}

    @pytest.mark.parametrize(
        "product_type",
        [
            pytest.param("faction_set", id="faction_set が card_pack_id を欠くとき、ValueError になる"),
            pytest.param("card_pack", id="card_pack が card_pack_id を欠くとき、ValueError になる"),
        ],
    )
    def test_card_pack_idが必須のtypeでキーが無ければValueErrorになる(self, tmp_path: Path, product_type):
        path = tmp_path / "products.yaml"
        _write_yaml(path, {"products": [{"product_id": "p1", "type": product_type}]})
        with pytest.raises(ValueError, match="requires 'card_pack_id'"):
            v.load_shop_card_pack_refs(path)

    @pytest.mark.parametrize(
        "product",
        [
            pytest.param(
                {"product_id": "p1", "type": "bundle"},
                id="未知の type のとき、card_pack 参照の要否を判定できずValueErrorになる",
            ),
            pytest.param(
                {"product_id": "p1"},
                id="type が無いとき、card_pack 参照の要否を判定できずValueErrorになる",
            ),
        ],
    )
    def test_分類できないtypeはValueErrorになる(self, tmp_path: Path, product):
        path = tmp_path / "products.yaml"
        _write_yaml(path, {"products": [product]})
        with pytest.raises(ValueError, match="unknown type"):
            v.load_shop_card_pack_refs(path)

    def test_product_idが無ければValueErrorになる(self, tmp_path: Path):
        path = tmp_path / "products.yaml"
        _write_yaml(path, {"products": [{"type": "faction_set", "card_pack_id": "x"}]})
        with pytest.raises(ValueError, match="requires 'product_id'"):
            v.load_shop_card_pack_refs(path)

    def test_top_levelのproductsキーが無ければValueErrorになる(self, tmp_path: Path):
        path = tmp_path / "products.yaml"
        _write_yaml(path, {"other_key": []})
        with pytest.raises(ValueError, match="top-level 'products' key is required"):
            v.load_shop_card_pack_refs(path)

    def test_productsの値が空のときリストでない異常としてValueErrorになる(self, tmp_path: Path):
        path = tmp_path / "products.yaml"
        path.write_text("products:\n", encoding="utf-8")
        with pytest.raises(ValueError, match="'products' must be a list"):
            v.load_shop_card_pack_refs(path)

    def test_productsが空リストのとき検証対象が無い異常としてValueErrorになる(self, tmp_path: Path):
        path = tmp_path / "products.yaml"
        path.write_text("products: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="'products' must not be empty"):
            v.load_shop_card_pack_refs(path)


class TestCardPackの一覧抽出:
    def test_全てのpack_idを抽出する(self, tmp_path: Path):
        path = tmp_path / "card_packs.yaml"
        _write_yaml(path, {
            "packs": [
                {"pack_id": "basic", "cards": []},
                {"pack_id": "faction_set_she", "cards": []},
            ],
        })
        assert v.load_card_pack_ids(path) == {"basic", "faction_set_she"}

    def test_top_levelのpacksキーが無ければValueErrorになる(self, tmp_path: Path):
        path = tmp_path / "card_packs.yaml"
        _write_yaml(path, {"other_key": []})
        with pytest.raises(ValueError, match="top-level 'packs' key is required"):
            v.load_card_pack_ids(path)

    def test_packsの値が空のときリストでない異常としてValueErrorになる(self, tmp_path: Path):
        path = tmp_path / "card_packs.yaml"
        path.write_text("packs:\n", encoding="utf-8")
        with pytest.raises(ValueError, match="'packs' must be a list"):
            v.load_card_pack_ids(path)

    def test_packsが空リストのとき検証対象が無い異常としてValueErrorになる(self, tmp_path: Path):
        path = tmp_path / "card_packs.yaml"
        path.write_text("packs: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="'packs' must not be empty"):
            v.load_card_pack_ids(path)

    def test_pack_idが無ければValueErrorになる(self, tmp_path: Path):
        path = tmp_path / "card_packs.yaml"
        _write_yaml(path, {"packs": [{"cards": []}]})
        with pytest.raises(ValueError, match="requires 'pack_id'"):
            v.load_card_pack_ids(path)


class Test欠落参照の検出:
    @pytest.mark.parametrize(
        ("shop_refs", "card_ids", "expected"),
        [
            pytest.param(
                {"fs_she": "faction_set_she"},
                {"basic", "faction_set_she"},
                [],
                id="全参照が存在するとき、空リストになる",
            ),
            pytest.param(
                {"fs_ghost": "faction_set_ghost"},
                {"basic"},
                [("fs_ghost", "faction_set_ghost")],
                id="欠落が1件のとき、product_id 付きで報告される",
            ),
            pytest.param(
                {"z": "z_pack", "a": "a_pack", "ok": "basic"},
                {"basic"},
                [("a", "a_pack"), ("z", "z_pack")],
                id="欠落が複数件のとき、product_id で整列して報告される",
            ),
        ],
    )
    def test_欠落したpack参照を検出する(self, shop_refs, card_ids, expected):
        assert v.find_missing(shop_refs, card_ids) == expected


class Test失敗通知の整形:
    def test_失敗通知に日付ヘッダと件数と各product_pack_idが載る(self):
        msg = v._format_failure_message(
            [("fs_ghost", "faction_set_ghost"), ("fs_lost", "limited_lost")],
            "2026-06-20",
            run_url="",
        )
        assert "[参照整合 2026-06-20]" in msg
        assert "2 件" in msg
        assert ":x:" in msg
        assert "fs_ghost" in msg
        assert "faction_set_ghost" in msg
        assert "fs_lost" in msg
        assert "limited_lost" in msg

    def test_run_urlがあればActionsログへの動線を載せる(self):
        msg = v._format_failure_message(
            [("p", "q")], "2026-06-20", run_url="https://github.com/org/repo/actions/runs/1"
        )
        assert "<https://github.com/org/repo/actions/runs/1|GitHub Actions ログ>" in msg

    def test_run_urlが空ならURL行を出さない(self):
        msg = v._format_failure_message([("p", "q")], "2026-06-20", run_url="")
        assert "GitHub Actions ログ" not in msg


class Test成功通知の整形:
    def test_成功通知はフリート書式の短文にする(self):
        msg = v._format_success_message("2026-06-20")
        assert ":white_check_mark:" in msg
        assert "[参照整合 2026-06-20]" in msg
        assert "card_pack OK" in msg


class TestCLIの終了コードとSlack通知:
    def _write_pair(self, tmp_path: Path, shop_products, card_packs):
        shop = tmp_path / "products.yaml"
        card = tmp_path / "card_packs.yaml"
        _write_yaml(shop, {"products": shop_products})
        _write_yaml(card, {"packs": card_packs})
        return shop, card

    def test_全参照が有効ならexit0で成功メッセージをSlackに送る(self, tmp_path: Path, capsys, monkeypatch):
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
        assert "card_pack OK" in msg
        assert "OK" in capsys.readouterr().out

    def test_不整合があればexit1で失敗詳細をSlackに送る(self, tmp_path: Path, capsys, monkeypatch):
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

    def test_SLACK_WEBHOOK_URL未設定は通知経路が無い異常としてexit1で落とす(self, tmp_path: Path, capsys, monkeypatch):
        # 他 Slack ジョブと同じく、通知経路の欠如は silent skip せず exit 1 で落とす。
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
