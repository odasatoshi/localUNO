"""ルールセット（プラグイン）と有効化リスト。

標準UNO をリファレンス実装として置き、ローカルルールを**記述順**の有効化リストで
積む（spec §3.3）。有効化リストは起動時に固定し、ゲームごとに切り替えない。
ローカルルールを足すときは :data:`ENABLED_RULES` の**末尾**に追記する（後ろほど上書きが
強く効く。制限ルールは制限したい許可ルールより後ろへ）。
"""

from __future__ import annotations

from ..engine.hooks import HookRegistry, build_registry
from . import standard
from .standard import setup_game

# 有効化リスト（起動時固定・記述順）。先頭は必ず standard。
ENABLED_RULES = [
    standard.RULES,
]


def registry() -> HookRegistry:
    """有効化リストからフック実行器を組み立てる（記述順を保存）。"""
    return build_registry(ENABLED_RULES)


__all__ = [
    "ENABLED_RULES",
    "registry",
    "setup_game",
    "standard",
]
