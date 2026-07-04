"""ローカルルール #39 上がり制限撤廃の試験（docs/house-rules.md §5）。

active な ENABLED_RULES（standard + ... + win_unrestricted）で Wild/Wild4 を最後の
1枚として出して上がれることを検証する。engine 無改修（rules/ で完結）。
"""

from __future__ import annotations

import random

from lUNO.engine.actions import PlayAction
from lUNO.engine.cards import DRAW4, WILD, CardInstance, CardType, Color
from lUNO.engine.engine import STANDARD_TURN_ACTIONS, apply_action
from lUNO.engine.hooks import build_registry
from lUNO.engine.state import GameState
from lUNO.rules import registry, standard, win_unrestricted


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def _state(last: CardInstance) -> GameState:
    """p1 が last の1枚だけ、場トップは赤7。"""
    return GameState(
        hands={"p1": (last,), "p2": (card("9", Color.GREEN, 4),)},
        draw_pile=(),
        discard_pile=(card("7", Color.RED, 3),),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )


def test_win_on_wild_via_active_ruleset():
    """有効化リスト実配列で Wild を最後の1枚に出して上がれる。"""
    out = apply_action(registry(), _state(card(WILD, None, 1)), PlayAction("p1", (1,)))
    assert out.winner == "p1"
    assert out.awaiting == {}


def test_win_on_wild_draw4_via_active_ruleset():
    """Wild Draw4 でも上がれる（上がりで相手への強制ドローは発生しない＝終局優先）。"""
    out = apply_action(registry(), _state(card(DRAW4, None, 1)), PlayAction("p1", (1,)))
    assert out.winner == "p1"
    assert out.pending_draw == 0  # 終局で pending はクリア
    assert out.awaiting == {}


def test_wild_still_playable_not_last_card():
    """最後の1枚でないワイルドは従来どおり出せる（回帰防止）。"""
    reg = build_registry([standard.RULES, win_unrestricted.RULES])
    st = GameState(
        hands={
            "p1": (card(WILD, None, 1), card("5", Color.RED, 2)),
            "p2": (card("9", Color.GREEN, 4),),
        },
        draw_pile=(),
        discard_pile=(card("7", Color.RED, 3),),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )
    out = apply_action(reg, st, PlayAction("p1", (1,)))
    assert out.discard_pile[-1].id == 1  # ワイルドが出せている
    assert out.winner is None  # まだ上がっていない（色選択待ち）
    assert out.awaiting == {"p1": ("choose_color",)}
