#!/usr/bin/env python3
from pathlib import Path

import pytest
from applied_union import (
    CommandResult,
    ImageCommandError,
    applied_image_ref,
    fetch_applied_union,
    record_applied_union,
)
from conftest import build_union
from schema_check import check

IMAGE_BASE = "example-registry/example-project/example-repo/db-migrate"
DOCKERFILE = "db-migrate/applied-union.Dockerfile"
CONTEXT_DIR = "db-migrate/sql"
CONTAINER_ID = "0123456789ab"

DEV_REF = f"{IMAGE_BASE}-applied-dev:latest"

ABSENT_PULL = CommandResult(
    1, "", 'Error response from daemon: manifest unknown: Failed to fetch "latest"'
)
ABSENT_REFERENCE_PULL = CommandResult(
    1,
    "",
    f'Error response from daemon: failed to resolve reference "{DEV_REF}": {DEV_REF}: not found',
)
DENIED_PULL = CommandResult(1, "", "Error response from daemon: denied: Permission denied")
UNREACHABLE_PULL = CommandResult(
    1, "", "Error response from daemon: failed to do request: dial tcp: connect: connection refused"
)
UNREACHABLE_REFERENCE_PULL = CommandResult(
    1,
    "",
    f'Error response from daemon: failed to resolve reference "{DEV_REF}": failed to do request: '
    'Head "https://example-registry/v2/example-project/example-repo/db-migrate-applied-dev'
    '/manifests/latest": dial tcp: connect: connection refused',
)
CREDENTIAL_HELPER_PULL = CommandResult(
    1,
    "",
    'error getting credentials - err: exec: "docker-credential-desktop": executable file '
    "not found in $PATH, out: ``",
)


class FakeDocker:
    """docker コマンドの代わりに、あらかじめ決めた結果を返す実行器。

    Args:
        failures: 失敗させる副コマンド名 (pull / create / cp / rm / build / push) と、その結果。
        stored_union: `docker cp` が取り出す union SQL。None のときは何も書き出さない。
    """

    def __init__(
        self,
        *,
        failures: dict[str, CommandResult] | None = None,
        stored_union: str | None = None,
    ):
        self._failures = failures or {}
        self._stored_union = stored_union
        self.commands: list[list[str]] = []
        self.containers: set[str] = set()

    def __call__(self, command: list[str]) -> CommandResult:
        self.commands.append(command)
        subcommand = command[1]
        if subcommand in self._failures:
            return self._failures[subcommand]
        if subcommand == "create":
            self.containers.add(CONTAINER_ID)
            return CommandResult(0, f"{CONTAINER_ID}\n", "")
        if subcommand == "rm":
            self.containers.discard(command[-1])
        if subcommand == "cp" and self._stored_union is not None:
            Path(command[3]).write_text(self._stored_union)
        return CommandResult(0, "", "")


class Test適用済みunionの保存先:
    @pytest.mark.parametrize(
        ("environment", "expected"),
        [
            pytest.param("dev", f"{IMAGE_BASE}-applied-dev:latest", id="dev の記録先は dev 専用のイメージになる"),
            pytest.param("stg", f"{IMAGE_BASE}-applied-stg:latest", id="stg の記録先は stg 専用のイメージになる"),
            pytest.param("prod", f"{IMAGE_BASE}-applied-prod:latest", id="prod の記録先は prod 専用のイメージになる"),
        ],
    )
    def test_環境ごとに別のイメージへ記録する(self, environment, expected):
        assert applied_image_ref(IMAGE_BASE, environment) == expected


class Test適用済みunionの取り出し:
    def test_記録があるとき比較元のファイルへ取り出す(self, tmp_path):
        docker = FakeDocker(stored_union="CREATE TABLE account.users (id UUID);")
        out = tmp_path / "baseline.sql"

        assert fetch_applied_union(IMAGE_BASE, "dev", str(out), run=docker) is True
        assert out.read_text() == "CREATE TABLE account.users (id UUID);"

    def test_対象環境のイメージから取り出す(self, tmp_path):
        docker = FakeDocker(stored_union="CREATE TABLE account.users (id UUID);")

        fetch_applied_union(IMAGE_BASE, "stg", str(tmp_path / "baseline.sql"), run=docker)

        assert docker.commands[0] == ["docker", "pull", f"{IMAGE_BASE}-applied-stg:latest"]

    def test_記録がまだ無いとき記録なしとして判定する(self, tmp_path):
        docker = FakeDocker(failures={"pull": ABSENT_PULL})

        found = fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

        assert found is False

    def test_記録がまだ無いとき比較元のファイルを作らない(self, tmp_path):
        docker = FakeDocker(failures={"pull": ABSENT_PULL})
        out = tmp_path / "baseline.sql"

        fetch_applied_union(IMAGE_BASE, "dev", str(out), run=docker)

        assert not out.exists()

    def test_記録がまだ無いとき前回の取り出しが残したファイルを消す(self, tmp_path):
        docker = FakeDocker(failures={"pull": ABSENT_PULL})
        out = tmp_path / "baseline.sql"
        out.write_text("CREATE TABLE account.users (id UUID);")

        fetch_applied_union(IMAGE_BASE, "dev", str(out), run=docker)

        assert not out.exists()

    def test_参照そのものが無いと答えられたとき記録なしとして判定する(self, tmp_path):
        docker = FakeDocker(failures={"pull": ABSENT_REFERENCE_PULL})

        found = fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

        assert found is False

    def test_資格情報ヘルパを起動できないとき記録なしと扱わずに中断する(self, tmp_path):
        docker = FakeDocker(failures={"pull": CREDENTIAL_HELPER_PULL})

        with pytest.raises(ImageCommandError, match="docker-credential-desktop"):
            fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

    def test_参照名を挙げた通信断のとき記録なしと扱わずに中断する(self, tmp_path):
        docker = FakeDocker(failures={"pull": UNREACHABLE_REFERENCE_PULL})

        with pytest.raises(ImageCommandError, match="connection refused"):
            fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

    def test_権限が無くて取得できないとき記録なしと扱わずに中断する(self, tmp_path):
        docker = FakeDocker(failures={"pull": DENIED_PULL})

        with pytest.raises(ImageCommandError, match="denied"):
            fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

    def test_レジストリに到達できないとき記録なしと扱わずに中断する(self, tmp_path):
        docker = FakeDocker(failures={"pull": UNREACHABLE_PULL})

        with pytest.raises(ImageCommandError, match="connection refused"):
            fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

    def test_取得できても取り出しに失敗したとき中断する(self, tmp_path):
        docker = FakeDocker(failures={"cp": CommandResult(1, "", "no such file or directory")})

        with pytest.raises(ImageCommandError, match="docker cp"):
            fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

    def test_取り出しに成功したとき取り出しに使ったコンテナが残らない(self, tmp_path):
        docker = FakeDocker(stored_union="CREATE TABLE account.users (id UUID);")

        fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

        assert docker.containers == set()

    def test_取り出しに失敗したとき取り出しに使ったコンテナが残らない(self, tmp_path):
        docker = FakeDocker(failures={"cp": CommandResult(1, "", "no such file or directory")})

        with pytest.raises(ImageCommandError, match="docker cp"):
            fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

        assert docker.containers == set()

    def test_取り出しにもコンテナの片付けにも失敗したとき取り出しの失敗を報告する(self, tmp_path, capsys):
        docker = FakeDocker(
            failures={
                "cp": CommandResult(1, "", "no such file or directory"),
                "rm": CommandResult(1, "", "container is already in use"),
            }
        )

        with pytest.raises(ImageCommandError, match="docker cp"):
            fetch_applied_union(IMAGE_BASE, "dev", str(tmp_path / "baseline.sql"), run=docker)

        assert "container is already in use" in capsys.readouterr().err


class Test適用済みunionの記録:
    def test_指定したコンテキストから対象環境のイメージを作って送出する(self):
        docker = FakeDocker()

        record_applied_union(IMAGE_BASE, "dev", DOCKERFILE, CONTEXT_DIR, run=docker)

        assert docker.commands == [
            ["docker", "build", "-f", DOCKERFILE, "-t", f"{IMAGE_BASE}-applied-dev:latest", CONTEXT_DIR],
            ["docker", "push", f"{IMAGE_BASE}-applied-dev:latest"],
        ]

    def test_送出に失敗したとき中断する(self):
        docker = FakeDocker(failures={"push": CommandResult(1, "", "denied: requested access to the resource is denied")})

        with pytest.raises(ImageCommandError, match="docker push"):
            record_applied_union(IMAGE_BASE, "dev", DOCKERFILE, CONTEXT_DIR, run=docker)

    def test_イメージを作れなかったとき送出せずに中断する(self):
        docker = FakeDocker(failures={"build": CommandResult(1, "", "failed to compute cache key")})

        with pytest.raises(ImageCommandError, match="docker build"):
            record_applied_union(IMAGE_BASE, "dev", DOCKERFILE, CONTEXT_DIR, run=docker)

        assert ["docker", "push", f"{IMAGE_BASE}-applied-dev:latest"] not in docker.commands


class Test取り出したunionを比較元にした破壊的変更の検出:
    def test_記録にあるテーブルとカラムが消えているとき削除として報告される(self, tmp_path):
        docker = FakeDocker(stored_union=build_union(("shop", """
        CREATE TABLE shop.products (product_id UUID PRIMARY KEY, price INTEGER);
        CREATE TABLE shop.subscriptions (subscription_id UUID PRIMARY KEY);
        """)))
        baseline = tmp_path / "baseline.sql"
        fetch_applied_union(IMAGE_BASE, "dev", str(baseline), run=docker)

        candidate = tmp_path / "candidate.sql"
        candidate.write_text(build_union(("shop", "CREATE TABLE shop.products (product_id UUID PRIMARY KEY);")))

        assert check(str(baseline), str(candidate)) == [
            "DROP TABLE: shop.subscriptions",
            "DROP COLUMN: shop.products.price",
        ]

    def test_記録と同じ内容を適用するとき警告しない(self, tmp_path):
        ddl = "CREATE TABLE shop.products (product_id UUID PRIMARY KEY, price INTEGER);"
        docker = FakeDocker(stored_union=build_union(("shop", ddl)))
        baseline = tmp_path / "baseline.sql"
        fetch_applied_union(IMAGE_BASE, "dev", str(baseline), run=docker)

        candidate = tmp_path / "candidate.sql"
        candidate.write_text(build_union(("shop", ddl)))

        assert check(str(baseline), str(candidate)) == []
