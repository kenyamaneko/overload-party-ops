#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path

from conftest import build_union

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / ".github" / "scripts" / "db-migrate" / "check-schema-safety.sh"

APPLIED_REF = "registry.example.com/example-project/example-repo/db-migrate-applied-dev:latest"

ABSENT_PULL_MESSAGE = 'manifest unknown: Failed to fetch "latest"'
ABSENT_REFERENCE_PULL_MESSAGE = (
    f'Error response from daemon: failed to resolve reference "{APPLIED_REF}": '
    f"{APPLIED_REF}: not found"
)
UNREACHABLE_PULL_MESSAGE = "failed to do request: dial tcp: connect: connection refused"
CREDENTIAL_HELPER_PULL_MESSAGE = (
    'error getting credentials - err: exec: "docker-credential-desktop": executable file '
    "not found in $PATH, out: ``"
)


def _install_failing_docker(tmp_path, message: str) -> Path:
    """指定した理由で必ず失敗する docker を用意し、それを置いたディレクトリを返します。

    Args:
        tmp_path: pytest が用意する一時ディレクトリ。
        message: docker が標準エラーへ出す失敗の理由。

    Returns:
        用意した docker を含むディレクトリ。PATH の先頭に置いて使う。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    # docker の文言には $PATH やバッククォートが現れるため、展開されない引用形式で埋め込む
    docker.write_text(f"#!/usr/bin/env bash\ncat >&2 <<'EOF'\n{message}\nEOF\nexit 1\n")
    docker.chmod(0o755)
    return bin_dir


def _run_check(tmp_path, *, bootstrap_baseline: str, pull_message: str) -> subprocess.CompletedProcess:
    """スキーマ安全チェックの入口を、記録の取得が失敗する状態で実行します。

    Args:
        tmp_path: pytest が用意する一時ディレクトリ。
        bootstrap_baseline: 初回適用の申告 (true / false)。
        pull_message: 記録の取得が失敗する理由。

    Returns:
        実行結果。
    """
    candidate = tmp_path / "candidate.sql"
    candidate.write_text(build_union(("account", "CREATE TABLE account.users (id UUID PRIMARY KEY);")))
    env = {
        **os.environ,
        "PATH": f"{_install_failing_docker(tmp_path, pull_message)}{os.pathsep}{os.environ['PATH']}",
        "REGISTRY": "registry.example.com",
        "AR_PROJECT": "example-project",
        "AR_REPOSITORY": "example-repo",
        "IMAGE_NAME": "db-migrate",
        "ENV": "dev",
        "BOOTSTRAP_BASELINE": bootstrap_baseline,
        "BASELINE_UNION": str(tmp_path / "baseline.sql"),
        "CANDIDATE_UNION": str(candidate),
    }
    return subprocess.run(
        [str(ENTRYPOINT)], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )


class Testスキーマ安全チェックの実行:
    def test_記録の有無を判別できない失敗のとき初回適用として実行しても中断する(self, tmp_path):
        result = _run_check(tmp_path, bootstrap_baseline="true", pull_message=UNREACHABLE_PULL_MESSAGE)

        assert result.returncode != 0
        assert "connection refused" in result.stdout + result.stderr

    def test_資格情報ヘルパを起動できない失敗のとき初回適用として実行しても中断する(self, tmp_path):
        result = _run_check(
            tmp_path, bootstrap_baseline="true", pull_message=CREDENTIAL_HELPER_PULL_MESSAGE
        )

        assert result.returncode != 0
        assert "docker-credential-desktop" in result.stdout + result.stderr

    def test_記録がまだ無いとき初回適用として実行すると検査せずに通る(self, tmp_path):
        result = _run_check(tmp_path, bootstrap_baseline="true", pull_message=ABSENT_PULL_MESSAGE)

        assert result.returncode == 0
        assert "NOT PERFORMED" in result.stdout
        assert "holds 1 table(s)" in result.stdout

    def test_参照そのものが無いと答えられたとき初回適用として実行すると検査せずに通る(self, tmp_path):
        result = _run_check(
            tmp_path, bootstrap_baseline="true", pull_message=ABSENT_REFERENCE_PULL_MESSAGE
        )

        assert result.returncode == 0
        assert "NOT PERFORMED" in result.stdout
        assert "holds 1 table(s)" in result.stdout

    def test_記録がまだ無いとき初回適用と申告しなければ比較元が無い旨を出力して止まる(self, tmp_path):
        result = _run_check(tmp_path, bootstrap_baseline="false", pull_message=ABSENT_PULL_MESSAGE)

        assert result.returncode == 4
        assert "no schema union has been recorded as applied yet" in result.stdout
