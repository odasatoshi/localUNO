"""ローカルルール #38 Draw2 スタックの試験（docs/house-rules.md §3）。

standard + draw2_stack を積んだ実効挙動を検証する。engine 無改修（rules/ で完結）。
"""

from __future__ import annotations

import random

import pytest

from lUNO.engine.actions import ChooseColorAction, DrawAction, PlayAction
from lUNO.engine.cards import DRAW2, DRAW4, CardInstance, CardType, Color
from lUNO.engine.engine import STANDARD_TURN_ACTIONS, IllegalAction, apply_action
from lUNO.engine.hooks import build_registry
from lUNO.engine.state import GameState
from lUNO.rules import draw2_stack, registry, standard


def _reg():
    return build_registry([standard.RULES, draw2_stack.RULES])


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def _state(p1, p2, top, draw=()):
    return GameState(
        hands={"p1": p1, "p2": p2},
        draw_pile=draw,
        discard_pile=(top,),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )


def test_receiver_can_return_draw2_color_agnostic():
    """Draw2 を出された受け手は、色違いの Draw2 でも1枚で返せる。累積して連鎖する。"""
    reg = _reg()
    st = _state(
        p1=(card(DRAW2, Color.RED, 1), card("5", Color.RED, 9)),
        p2=(card(DRAW2, Color.BLUE, 2), card("9", Color.GREEN, 3)),  # 青 Draw2（色違い）
        top=card("7", Color.RED, 4),
    )
    after = apply_action(reg, st, PlayAction("p1", (1,)))
    assert after.pending_draw == 2
    assert after.current_player == "p2"
    assert set(after.awaiting["p2"]) == {"draw", "play"}  # 引く or 返す

    back = apply_action(reg, after, PlayAction("p2", (2,)))  # 青 Draw2 で返す（色不問）
    assert back.pending_draw == 4  # 累積
    assert back.current_player == "p1"
    assert set(back.awaiting["p1"]) == {"draw", "play"}


def test_non_draw2_blocked_during_stack():
    """Draw2 累積中は Draw2 以外を出せない（色一致でも却下）。"""
    reg = _reg()
    st = _state(
        p1=(card(DRAW2, Color.RED, 1), card("5", Color.RED, 9)),
        p2=(card("7", Color.RED, 2), card("9", Color.GREEN, 3)),  # 赤7 は色一致だが Draw2 でない
        top=card("7", Color.RED, 4),
    )
    after = apply_action(reg, st, PlayAction("p1", (1,)))
    with pytest.raises(IllegalAction):
        apply_action(reg, after, PlayAction("p2", (2,)))


def test_not_returned_draws_accumulated():
    """返さず引く側が累積分を全部引き、pending は 0 に戻り手番が進む。"""
    reg = _reg()
    st = _state(
        p1=(card(DRAW2, Color.RED, 1), card("5", Color.RED, 9)),
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 4),
        draw=(card("1", Color.BLUE, 5), card("2", Color.YELLOW, 6)),
    )
    after = apply_action(reg, st, PlayAction("p1", (1,)))
    drawn = apply_action(reg, after, DrawAction("p2"))
    assert len(drawn.hands["p2"]) == 1 + 2  # 累積2枚を引いた
    assert drawn.pending_draw == 0
    assert drawn.current_player == "p1"  # 手番が戻る


def test_draw4_pending_does_not_allow_draw2_stack():
    """Draw4 の累積中は受理集合に play が無く、Draw2 で返せない（§3 Draw4 は無関係）。"""
    reg = _reg()
    st = _state(
        p1=(card(DRAW4, None, 1), card("5", Color.RED, 9)),
        p2=(card(DRAW2, Color.GREEN, 2), card("9", Color.GREEN, 3)),
        top=card("7", Color.RED, 4),
    )
    after = apply_action(reg, st, PlayAction("p1", (1,)))  # Draw4 → 色選択待ち
    after = apply_action(reg, after, ChooseColorAction("p1", Color.RED))  # 色確定→相手引くのみ
    assert after.awaiting == {"p2": ("draw",)}  # play 無し
    with pytest.raises(IllegalAction):
        apply_action(reg, after, PlayAction("p2", (2,)))  # Draw2 で返せない


def test_stack_via_active_ruleset():
    """有効化リスト実配列でも Draw2 スタックが効く（他ハウスルールと非干渉）。"""
    st = _state(
        p1=(card(DRAW2, Color.RED, 1), card("5", Color.RED, 9)),
        p2=(card(DRAW2, Color.BLUE, 2),),
        top=card("7", Color.RED, 4),
    )
    after = apply_action(registry(), st, PlayAction("p1", (1,)))
    assert set(after.awaiting["p2"]) == {"draw", "play"}
