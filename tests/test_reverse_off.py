"""ローカルルール #36 リバース無効化の試験（docs/house-rules.md §1）。

standard + reverse_off を積んだ実効挙動を検証する。engine は無改修（rules/ で完結）。
"""

from __future__ import annotations

import random

from lUNO.engine.actions import PlayAction
from lUNO.engine.cards import REVERSE, SKIP, CardInstance, CardType, Color
from lUNO.engine.engine import STANDARD_TURN_ACTIONS, apply_action
from lUNO.engine.hooks import build_registry
from lUNO.engine.state import GameState
from lUNO.rules import reverse_off, standard


def _reg():
    return build_registry([standard.RULES, reverse_off.RULES])


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def _state(first_card: CardInstance) -> GameState:
    """p1 が first_card ＋ 赤5 を持ち、場トップは赤3。"""
    return GameState(
        hands={
            "p1": (first_card, card("5", Color.RED, 2)),
            "p2": (card("9", Color.GREEN, 4),),
        },
        draw_pile=(),
        discard_pile=(card("3", Color.RED, 3),),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )


def test_reverse_passes_turn_to_opponent():
    """リバースは無効効果 → 標準のスキップ化を打ち消し、手番は相手へ移る。"""
    st = _state(card(REVERSE, Color.RED, 1))
    out = apply_action(_reg(), st, PlayAction("p1", (1,)))
    assert out.discard_pile[-1].id == 1  # リバースが出ている
    assert out.current_player == "p2"  # 相手へ手番が移る（スキップ化しない）
    assert out.awaiting == {"p2": STANDARD_TURN_ACTIONS}


def test_skip_still_keeps_turn():
    """スキップは本ルールの対象外 → 標準どおり自分の手番が続く（回帰防止）。"""
    st = _state(card(SKIP, Color.RED, 1))
    out = apply_action(_reg(), st, PlayAction("p1", (1,)))
    assert out.current_player == "p1"  # スキップは自分の手番継続のまま
    assert out.awaiting == {"p1": STANDARD_TURN_ACTIONS}


def test_win_on_reverse_still_valid():
    """リバースを最後の1枚として出しても上がりは成立する（winner 確定を壊さない）。"""
    st = GameState(
        hands={"p1": (card(REVERSE, Color.RED, 1),), "p2": (card("9", Color.GREEN, 4),)},
        draw_pile=(),
        discard_pile=(card("3", Color.RED, 3),),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )
    out = apply_action(_reg(), st, PlayAction("p1", (1,)))
    assert out.winner == "p1"
    assert out.awaiting == {}  # 終局で受理集合は閉じる
