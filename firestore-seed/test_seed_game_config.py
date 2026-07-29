#!/usr/bin/env python3
import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import seed_game_config


class Testgame_config初期値の読み取り:
    def _write_yaml(self, tmp_path: Path, text: str) -> Path:
        """YAML 文字列を一時ファイルに書き出してパスを返す。

        Args:
            tmp_path: pytest が用意する一時ディレクトリ。
            text: 書き込む YAML 文字列。

        Returns:
            作成した一時ファイルのパス。
        """
        path = tmp_path / "defaults.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    @pytest.mark.parametrize(
        "yaml_text",
        [
            pytest.param("other: {}\n", id="defaults キーが無いとき"),
            pytest.param("- a\n- b\n", id="YAML 全体がマッピングでないとき"),
        ],
    )
    def test_トップレベル構造が不正なときSystemExitで中断する(self, tmp_path, yaml_text):
        path = self._write_yaml(tmp_path, yaml_text)
        with pytest.raises(SystemExit, match="defaults"):
            seed_game_config.load_defaults(path)

    @pytest.mark.parametrize(
        "yaml_text",
        [
            pytest.param("defaults: {}\n", id="defaults が空のとき"),
            pytest.param("defaults: [1]\n", id="defaults がマッピングでないとき"),
        ],
    )
    def test_defaultsが非空マッピングでないときSystemExitで中断する(self, tmp_path, yaml_text):
        path = self._write_yaml(tmp_path, yaml_text)
        with pytest.raises(SystemExit, match="non-empty"):
            seed_game_config.load_defaults(path)

    def test_エントリにvalueキーが無いときキー名付きのSystemExitで中断する(self, tmp_path):
        path = self._write_yaml(tmp_path, "defaults:\n  max_hp: {}\n")
        with pytest.raises(SystemExit, match="max_hp"):
            seed_game_config.load_defaults(path)

    def test_エントリがマッピングでないときSystemExitで中断する(self, tmp_path):
        path = self._write_yaml(tmp_path, "defaults:\n  max_hp: 30\n")
        with pytest.raises(SystemExit):
            seed_game_config.load_defaults(path)

    def test_正常のときキーとvalueの辞書になる(self, tmp_path):
        path = self._write_yaml(
            tmp_path,
            "defaults:\n  max_hp:\n    value: 30\n  flag:\n    value: true\n",
        )
        assert seed_game_config.load_defaults(path) == {"max_hp": 30, "flag": True}


class Testlock経由の取得:
    """subprocess は外部境界としてダブル化する。checkout 相当のコマンド実行時に
    workdir 配下へ期待ファイルを書く fake で、以降のファイル存在チェック分岐を通す。
    """

    def _write_lock_yaml(self, tmp_path: Path, text: str) -> Path:
        """lock YAML 文字列を一時ファイルに書き出してパスを返す。

        Args:
            tmp_path: pytest が用意する一時ディレクトリ。
            text: 書き込む YAML 文字列。

        Returns:
            作成した一時ファイルのパス。
        """
        path = tmp_path / "seed-sources.lock.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def _fake_subprocess_run(self, target_relpath: str, *, create_target: bool = True):
        """seed_game_config.subprocess.run の fake を作る。

        Args:
            target_relpath: workdir からの取得対象ファイルの相対パス。
            create_target: True なら checkout 相当のコマンド実行時に target を書く。

        Returns:
            (calls, envs, fake) のタプル。calls は呼び出された cmd、
            envs は同じ順で渡された環境変数のリスト。
        """
        calls: list[list[str]] = []
        envs: list[dict[str, str] | None] = []

        def fake(cmd, cwd=None, env=None, check=None):
            calls.append(cmd)
            envs.append(env)
            if create_target and cmd[:2] == ["git", "checkout"]:
                target = Path(cwd) / target_relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("defaults:\n  max_hp:\n    value: 30\n")
            return MagicMock()

        return calls, envs, fake

    _LOCK_ENTRY = "sources:\n  game_config:\n    repo: o/r\n    path: data/defaults.yaml\n"

    def test_sourcesキーが無いときSystemExitで中断する(self, tmp_path):
        lock_path = self._write_lock_yaml(tmp_path, "x: {}\n")
        with pytest.raises(SystemExit, match="sources"):
            seed_game_config.fetch_from_lock(lock_path, tmp_path / "work", None)

    def test_sourcesにgame_configが無いときSystemExitで中断する(self, tmp_path):
        lock_path = self._write_lock_yaml(tmp_path, "sources: {}\n")
        with pytest.raises(SystemExit, match="sources.game_config"):
            seed_game_config.fetch_from_lock(lock_path, tmp_path / "work", None)

    def test_refが無いときmainを取得する(self, tmp_path):
        lock_path = self._write_lock_yaml(tmp_path, self._LOCK_ENTRY)
        calls, _, fake = self._fake_subprocess_run("data/defaults.yaml")
        with patch("seed_game_config.subprocess.run", side_effect=fake):
            seed_game_config.fetch_from_lock(lock_path, tmp_path / "work", None)
        fetch_calls = [c for c in calls if c[:2] == ["git", "fetch"]]
        assert fetch_calls[0][-1] == "main"

    def test_tokenがあるとき取得元URLにtokenを含めない(self, tmp_path):
        lock_path = self._write_lock_yaml(tmp_path, self._LOCK_ENTRY)
        calls, _, fake = self._fake_subprocess_run("data/defaults.yaml")
        with patch("seed_game_config.subprocess.run", side_effect=fake):
            seed_game_config.fetch_from_lock(lock_path, tmp_path / "work", "TSTTOKEN")
        remote_calls = [c for c in calls if c[:2] == ["git", "remote"]]
        assert remote_calls[0][-1] == "https://github.com/o/r.git"
        assert not any("TSTTOKEN" in arg for cmd in calls for arg in cmd)

    def test_tokenがあるときgitの設定として環境変数でtokenを渡す(self, tmp_path):
        lock_path = self._write_lock_yaml(tmp_path, self._LOCK_ENTRY)
        _, envs, fake = self._fake_subprocess_run("data/defaults.yaml")
        with patch("seed_game_config.subprocess.run", side_effect=fake):
            seed_game_config.fetch_from_lock(lock_path, tmp_path / "work", "TSTTOKEN")
        assert envs[0]["GIT_CONFIG_COUNT"] == "1"
        assert envs[0]["GIT_CONFIG_KEY_0"] == (
            "url.https://x-access-token:TSTTOKEN@github.com/.insteadOf"
        )
        assert envs[0]["GIT_CONFIG_VALUE_0"] == "https://github.com/"

    def test_tokenが無いとき認証の設定を付けない(self, tmp_path):
        lock_path = self._write_lock_yaml(tmp_path, self._LOCK_ENTRY)
        _, envs, fake = self._fake_subprocess_run("data/defaults.yaml")
        with patch("seed_game_config.subprocess.run", side_effect=fake):
            seed_game_config.fetch_from_lock(lock_path, tmp_path / "work", None)
        assert "GIT_CONFIG_COUNT" not in envs[0]

    def test_tokenが無いとき取得元URLは公開リポジトリのURLになる(self, tmp_path):
        lock_path = self._write_lock_yaml(tmp_path, self._LOCK_ENTRY)
        calls, _, fake = self._fake_subprocess_run("data/defaults.yaml")
        with patch("seed_game_config.subprocess.run", side_effect=fake):
            seed_game_config.fetch_from_lock(lock_path, tmp_path / "work", None)
        remote_calls = [c for c in calls if c[:2] == ["git", "remote"]]
        assert remote_calls[0][-1] == "https://github.com/o/r.git"

    def test_取得後に期待ファイルが無いときstale_lockとしてSystemExitで中断する(self, tmp_path):
        lock_path = self._write_lock_yaml(tmp_path, self._LOCK_ENTRY)
        _, _, fake = self._fake_subprocess_run("data/defaults.yaml", create_target=False)
        with patch("seed_game_config.subprocess.run", side_effect=fake):
            with pytest.raises(SystemExit, match="stale"):
                seed_game_config.fetch_from_lock(lock_path, tmp_path / "work", None)

    def test_取得が成功したとき取得したファイルのパスを返す(self, tmp_path):
        lock_path = self._write_lock_yaml(tmp_path, self._LOCK_ENTRY)
        _, _, fake = self._fake_subprocess_run("data/defaults.yaml")
        workdir = tmp_path / "work"
        with patch("seed_game_config.subprocess.run", side_effect=fake):
            result = seed_game_config.fetch_from_lock(lock_path, workdir, None)
        assert result == workdir / "data/defaults.yaml"


class TestFirestoreへの投入:
    """Firestore SDK は外部境界なので set() に渡るペイロードを直接観測してよい。"""

    def _build_fake_firestore_client(self, exists_by_key: dict[str, bool]):
        """firestore.Client の fake クラスを作る。

        Args:
            exists_by_key: ドキュメントキー -> 既存有無。

        Returns:
            (fake_client_class, set_calls) のタプル。set_calls は
            (key, payload) のリストとして set() 呼び出しを記録する。
        """
        set_calls: list[tuple[str, dict]] = []

        class _FakeDocRef:
            def __init__(self, key):
                self._key = key

            def get(self):
                snapshot = MagicMock()
                snapshot.exists = exists_by_key.get(self._key, False)
                return snapshot

            def set(self, payload):
                set_calls.append((self._key, payload))

        class _FakeCollection:
            def document(self, key):
                return _FakeDocRef(key)

        class _FakeClient:
            def __init__(self, project=None):
                self.project = project

            def collection(self, name):
                return _FakeCollection()

        return _FakeClient, set_calls

    def test_既存ドキュメントがあり上書き指定が無いとき保存せずスキップする(self, capsys):
        fake_client, set_calls = self._build_fake_firestore_client({"max_hp": True})
        with patch("seed_game_config.firestore.Client", fake_client):
            seed_game_config.seed("proj", {"max_hp": 30}, overwrite=False)
        assert set_calls == []
        assert "skip (exists): max_hp" in capsys.readouterr().out

    def test_既存が無いときvalueフィールドに包んで保存される(self):
        fake_client, set_calls = self._build_fake_firestore_client({"max_hp": False})
        with patch("seed_game_config.firestore.Client", fake_client):
            seed_game_config.seed("proj", {"max_hp": 30}, overwrite=False)
        assert set_calls == [("max_hp", {"value": 30})]

    def test_上書き指定のとき既存ドキュメントも保存し直す(self):
        fake_client, set_calls = self._build_fake_firestore_client({"max_hp": True})
        with patch("seed_game_config.firestore.Client", fake_client):
            seed_game_config.seed("proj", {"max_hp": 30}, overwrite=True)
        assert set_calls == [("max_hp", {"value": 30})]

    def test_値が0件のとき何も保存されない(self):
        fake_client, set_calls = self._build_fake_firestore_client({})
        with patch("seed_game_config.firestore.Client", fake_client):
            seed_game_config.seed("proj", {}, overwrite=False)
        assert set_calls == []

    def test_値が複数のとき全キーが保存される(self):
        fake_client, set_calls = self._build_fake_firestore_client({"max_hp": False, "flag": False})
        with patch("seed_game_config.firestore.Client", fake_client):
            seed_game_config.seed("proj", {"max_hp": 30, "flag": True}, overwrite=False)
        assert set_calls == [("max_hp", {"value": 30}), ("flag", {"value": True})]


class Testseed元の解決:
    def _namespace(self, *, source=None, fetch=False, lock="lock.yaml", token=None):
        """resolve_source に渡す argparse.Namespace を組み立てる。

        Args:
            source: --source の値。
            fetch: --fetch の指定有無。
            lock: --lock の値。
            token: --token の値。

        Returns:
            resolve_source が参照する属性を持つ Namespace。
        """
        return argparse.Namespace(source=source, fetch=fetch, lock=lock, token=token)

    def test_sourceのファイルが存在するときそのパスが使われる(self, tmp_path):
        source = tmp_path / "defaults.yaml"
        source.write_text("defaults:\n  max_hp:\n    value: 30\n")
        args = self._namespace(source=str(source))
        assert seed_game_config.resolve_source(args) == source

    def test_sourceのファイルが無いときSystemExitで中断する(self, tmp_path):
        args = self._namespace(source=str(tmp_path / "missing.yaml"))
        with pytest.raises(SystemExit, match="--source file not found"):
            seed_game_config.resolve_source(args)

    def test_fetchのときlock経由で取得したファイルが使われる(self, tmp_path):
        fetched = tmp_path / "fetched.yaml"
        args = self._namespace(fetch=True)
        with patch("seed_game_config.fetch_from_lock", return_value=fetched) as fetch_from_lock:
            result = seed_game_config.resolve_source(args)
        assert result == fetched
        fetch_from_lock.assert_called_once()

    def test_どちらも指定しないときSystemExitで使い方を示す(self):
        args = self._namespace()
        with pytest.raises(SystemExit, match="must specify one of"):
            seed_game_config.resolve_source(args)

    def test_両方指定したときローカルのsourceが優先される(self, tmp_path):
        source = tmp_path / "defaults.yaml"
        source.write_text("defaults:\n  max_hp:\n    value: 30\n")
        args = self._namespace(source=str(source), fetch=True)
        with patch("seed_game_config.fetch_from_lock") as fetch_from_lock:
            result = seed_game_config.resolve_source(args)
        assert result == source
        fetch_from_lock.assert_not_called()
