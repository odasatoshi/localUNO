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


# "main" を接頭辞に含む別ブランチを前方一致で誤拒否しないこと（回帰）
@pytest.mark.parametrize(
    "branch", ["develop", "feat/foo", "fix/bar", "mainline", "main-x"]
)
def test_allows_non_protected_branches(branch: str) -> None:
    result = _run(f"refs/heads/{branch} {_SHA_A} refs/heads/{branch} {_SHA_B}\n")
    assert result.returncode == 0


def test_allows_tag_push() -> None:
    # refs/tags/* は refs/heads/main と非一致 → 許可
    result = _run(f"refs/tags/v1.0 {_SHA_A} refs/tags/v1.0 {_SHA_B}\n")
    assert result.returncode == 0


def test_blocks_when_any_ref_among_many_is_main() -> None:
    # 複数 ref のうち1つでも main 宛なら拒否（while ループの肝）
    stdin = (
        f"refs/heads/feat/x {_SHA_A} refs/heads/feat/x {_SHA_B}\n"
        f"refs/heads/main {_SHA_A} refs/heads/main {_SHA_B}\n"
    )
    result = _run(stdin)
    assert result.returncode != 0
    assert "main" in result.stderr


def test_e2e_push_via_core_hookspath(tmp_path: Path) -> None:
    """実 git push を core.hooksPath 経由で検証（issue #5 完了条件の直接担保）。

    main 宛 push は拒否され、feature 宛 push は成功すること。
    """
    git = shutil.which("git")
    if not git:
        pytest.skip("git が見つからない")

    remote = tmp_path / "remote.git"
    subprocess.run([git, "init", "--bare", str(remote)], check=True, capture_output=True)

    work = tmp_path / "work"
    work.mkdir()

    def g(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git, "-C", str(work), *args], capture_output=True, text=True, check=check
        )

    g("init")
    g("config", "user.email", "test@example.com")
    g("config", "user.name", "test")
    # 実運用と同じく実際の .githooks を hooksPath に設定
    g("config", "core.hooksPath", str(HOOK.parent))
    g("checkout", "-b", "main")
    (work / "f.txt").write_text("x")
    g("add", "-A")
    g("commit", "-m", "init")
    g("remote", "add", "origin", str(remote))

    # main への push は拒否される
    blocked = g("push", "origin", "main", check=False)
    assert blocked.returncode != 0, "main への push が拒否されなかった"
    assert "main" in blocked.stderr

    # feature ブランチの push は通る
    g("checkout", "-b", "feat/x")
    allowed = g("push", "origin", "feat/x", check=False)
    assert allowed.returncode == 0, f"feature push が失敗: {allowed.stderr}"
