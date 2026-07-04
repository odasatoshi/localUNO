"""pre-push フックの挙動テスト: main 宛は拒否、feature 宛は許可。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".githooks" / "pre-push"

# git が渡す標準入力 1 行の形式: <local_ref> <local_sha> <remote_ref> <remote_sha>
_SHA_A = "1111111111111111111111111111111111111111"
_SHA_B = "2222222222222222222222222222222222222222"


def _run(stdin: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash, "bash が見つからない"
    return subprocess.run(
        [bash, str(HOOK), "origin", "git@example.com:owner/repo.git"],
        input=stdin,
        capture_output=True,
        text=True,
    )


def test_hook_is_executable() -> None:
    assert HOOK.exists()
    import os

    assert os.access(HOOK, os.X_OK), "pre-push に実行権限がない"


def test_blocks_push_to_main() -> None:
    result = _run(f"refs/heads/feat/x {_SHA_A} refs/heads/main {_SHA_B}\n")
    assert result.returncode != 0
    assert "main" in result.stderr


def test_blocks_delete_of_main() -> None:
    # 削除 push（local_sha が全ゼロ）でも宛先が main なら拒否
    zero = "0" * 40
    result = _run(f"(delete) {zero} refs/heads/main {_SHA_B}\n")
    assert result.returncode != 0


def test_allows_feature_branch() -> None:
    result = _run(f"refs/heads/feat/x {_SHA_A} refs/heads/feat/x {_SHA_B}\n")
    assert result.returncode == 0


def test_allows_empty_stdin() -> None:
    # push 対象が無い場合は素通し
    result = _run("")
    assert result.returncode == 0


@pytest.mark.parametrize("branch", ["develop", "feat/foo", "fix/bar"])
def test_allows_non_protected_branches(branch: str) -> None:
    result = _run(f"refs/heads/{branch} {_SHA_A} refs/heads/{branch} {_SHA_B}\n")
    assert result.returncode == 0
