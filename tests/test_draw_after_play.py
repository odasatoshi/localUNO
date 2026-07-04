"""ローカルルール #40 ドロー後プレイ／自主ドローの試験（docs/house-rules.md §7）。

active な ENABLED_RULES で、自主ドロー後の play/pass、引いた札を先頭とする制約、
強制ドロー（Draw2）が本ルールに巻き込まれないことを検証する。
"""

from __future__ import annotations

import random

import pytest

from lUNO.engine.actions import DrawAction, PassAction, PlayAction
from lUNO.engine.cards import DRAW2, CardInstance, CardType, Color
from lUNO.engine.engine import IllegalAction, apply_action
from lUNO.engine.state import GameState
from lUNO.rules import registry


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def _state(p1, p2, top, draw=()):
    return GameState(
        hands={"p1": p1, "p2": p2},
        draw_pile=draw,
        discard_pile=(top,),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": ("play", "draw")},
    )


def apply(state, action):
    return apply_action(registry(), state, action)


def _after_voluntary_draw():
    """p1 が自主ドローで赤0(id7)を引いた直後の state を返す。"""
    st = _state(
        p1=(card("5", Color.RED, 1),),  # 出せる赤5を持つが…
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 5),
        draw=(card("2", Color.BLUE, 6), card("0", Color.RED, 7)),  # 末尾=引く札=赤0
    )
    return apply(st, DrawAction("p1"))


def test_voluntary_draw_offers_play_or_pass():
    out = _after_voluntary_draw()
    assert out.current_player == "p1"  # 手番保持（既定の手番送りをしない）
    assert out.drawn_card_id == 7
    assert set(out.awaiting["p1"]) == {"play", "pass"}
    assert len(out.hands["p1"]) == 2  # 1枚だけ引いた


def test_play_drawn_card_after_draw():
    st = _after_voluntary_draw()
    out = apply(st, PlayAction("p1", (7,)))  # 引いた赤0を出す
    assert out.discard_pile[-1].id == 7
    assert out.drawn_card_id is None  # マーカー解除
    assert out.current_player == "p2"  # 手番送り


def test_pass_after_draw():
    st = _after_voluntary_draw()
    out = apply(st, PassAction("p1"))
    assert out.current_player == "p2"
    assert out.drawn_card_id is None
    assert len(out.hands["p1"]) == 2  # 引いた札は手札に残る


def test_cannot_play_non_drawn_card_after_draw():
    """ドロー後は引いた札を先頭にした出しだけ許す（手持ちの別札は出せない）。"""
    st = _after_voluntary_draw()  # 引いた札=赤0(7)、手札に赤5(1)もある
    with pytest.raises(IllegalAction):
        apply(st, PlayAction("p1", (1,)))  # 引いていない赤5 は出せない


def test_cannot_draw_again_after_draw():
    st = _after_voluntary_draw()
    with pytest.raises(IllegalAction):
        apply(st, DrawAction("p1"))  # awaiting は (play, pass)、draw 不可


def test_drawn_unplayable_then_pass():
    """引いた札が場に出せない場合は出せず、パスで手番が進む。"""
    st = _state(
        p1=(card("5", Color.RED, 1),),
        p2=(card("4", Color.GREEN, 3),),
        top=card("7", Color.RED, 5),
        draw=(card("9", Color.GREEN, 7),),  # 赤7 に出せない緑9
    )
    st = apply(st, DrawAction("p1"))
    assert st.drawn_card_id == 7
    with pytest.raises(IllegalAction):
        apply(st, PlayAction("p1", (7,)))  # 緑9 は赤7 に出せない
    passed = apply(st, PassAction("p1"))
    assert passed.current_player == "p2"


def test_multi_play_after_draw_with_drawn_lead():
    """引いた札を先頭に、同記号の手札を重ねて出せる（複数枚出しとの併用）。"""
    st = _state(
        p1=(card("0", Color.BLUE, 2), card("3", Color.GREEN, 8)),  # 青0＋緑3（上がり回避用）
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 5),
        draw=(card("0", Color.RED, 7),),  # 引く札=赤0
    )
    st = apply(st, DrawAction("p1"))  # 引いた赤0(7)。手札=(青0 id2, 緑3 id8, 赤0 id7)
    out = apply(st, PlayAction("p1", (7, 2)))  # 引いた赤0を先頭に青0を重ねる（共に "0"）
    assert [c.id for c in out.discard_pile[-2:]] == [7, 2]
    assert out.drawn_card_id is None
    assert out.current_player == "p2"


def test_forced_draw2_does_not_offer_play():
    """強制ドロー（Draw2 で複数引く）はドロー後プレイに巻き込まれず手番が進む。"""
    st = _state(
        p1=(card(DRAW2, Color.RED, 1), card("5", Color.RED, 9)),
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 5),
        draw=(card("0", Color.RED, 6), card("1", Color.BLUE, 7)),
    )
    after = apply(st, PlayAction("p1", (1,)))  # Draw2 → p2 は pending2・{draw,play}
    drawn = apply(after, DrawAction("p2"))  # p2 が強制ドロー2枚
    assert drawn.current_player == "p1"  # 手番が戻る（play/pass を挟まない）
    assert drawn.drawn_card_id is None
