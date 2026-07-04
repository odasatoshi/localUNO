"""ルールセット（プラグイン）と有効化リスト。

標準UNO をリファレンス実装として置き、ローカルルールを**記述順**の有効化リストで
積む（spec §3.3）。有効化リストは起動時に固定し、ゲームごとに切り替えない。
ローカルルールを足すときは :data:`ENABLED_RULES` の**末尾**に追記する（後ろほど上書きが
強く効く。制限ルールは制限したい許可ルールより後ろへ）。
"""

from __future__ import annotations

from ..engine.hooks import HookRegistry, build_registry
from . import (
    draw2_stack,
    draw_after_play,
    jump_in,
    multi_play,
    reverse_off,
    standard,
    uno_call,
    win_unrestricted,
)
from .standard import setup_game

# 有効化リスト（起動時固定・記述順）。先頭は必ず standard。
# 以降はハウスルール（docs/house-rules.md）を記述順に積む（後ろほど上書きが強い）。
ENABLED_RULES = [
    standard.RULES,
    reverse_off.RULES,  # #36 リバース無効化（§1）
    win_unrestricted.RULES,  # #39 上がり制限撤廃（§5）
    draw2_stack.RULES,  # #38 Draw2 スタック（§3）
    multi_play.RULES,  # #37 複数枚出し（§2）
    uno_call.RULES,  # #41 UNO 宣言＋指摘（§6）
    jump_in.RULES,  # #27 ジャンプイン（手番外で完全一致なら割り込み可）
    draw_after_play.RULES,  # #40 ドロー後プレイ／自主ドロー（§7）
]


def registry() -> HookRegistry:
    """有効化リストからフック実行器を組み立てる（記述順を保存）。"""
    return build_registry(ENABLED_RULES)


__all__ = [
    "ENABLED_RULES",
    "registry",
    "setup_game",
    "standard",
    "reverse_off",
    "win_unrestricted",
    "draw2_stack",
    "multi_play",
    "uno_call",
    "jump_in",
    "draw_after_play",
]
