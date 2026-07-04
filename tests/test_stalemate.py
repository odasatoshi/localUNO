"""ローカルルール #74 山切れ行き詰まりの引き分け決着の試験（docs/house-rules §8）。

active な ENABLED_RULES で、山札が補充不能かつ両者とも出せない行き詰まりが引き分け
（is_draw）で終局し、無限ドロー（ライブロック）が止まることを検証する。誤検出しない
条件（相手が出せる／山を再補充できる／ワイルド保持）も固定する。
"""

from __future__ import annotations

import random

import pytest

from lUNO.engine.actions import DrawAction, PlayAction
from lUNO.engine.cards import CardInstance, CardType, Color
from lUNO.engine.engine import IllegalAction, apply_action
from lUNO.engine.state import GameState
from lUNO.rules import registry


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def apply(state, action):
    return apply_action(registry(), state, action)


def _state(p1, p2, top, draw=(), current="p1", pending=0, awaiting=None):
    return GameState(
        hands={"p1": p1, "p2": p2},
        draw_pile=draw,
        discard_pile=(top,) if isinstance(top, CardInstance) else top,
        current_player=current,
        rng_state=random.Random(0).getstate(),
        pending_draw=pending,
        awaiting=awaiting or {current: ("play", "draw")},
    )


def test_stalemate_draw_on_unrefillable_deck():
    """山空・捨て山トップのみ・両者不出 → 1回のドローで引き分け終局（ライブロック解消）。"""
    st = _state(
        p1=(card("9", Color.GREEN, 1),),
        p2=(card("8", Color.BLUE, 2),),
        top=card("5", Color.RED, 100),  # 誰も出せない
    )
    out = apply(st, DrawAction("p1"))
    assert out.is_draw is True
    assert out.winner is None
    assert dict(out.awaiting) == {}  # 以降アクションを受理しない
    assert len(out.hands["p1"]) == 1  # 0枚引き（手札不変）


def test_no_action_accepted_after_stalemate_draw():
    """引き分け後は play も draw も受理されない（終局を engine が閉じる）。"""
    st = _state(
        p1=(card("9", Color.GREEN, 1),),
        p2=(card("8", Color.BLUE, 2),),
        top=card("5", Color.RED, 100),
    )
    out = apply(st, DrawAction("p1"))
    with pytest.raises(IllegalAction):
        apply(out, DrawAction("p2"))
    with pytest.raises(IllegalAction):
        apply(out, PlayAction("p2", (2,)))


def test_no_stalemate_when_opponent_can_play():
    """相手がトップに出せる札を持つなら行き詰まりでない（通常進行）。"""
    st = _state(
        p1=(card("9", Color.GREEN, 1),),
        p2=(card("3", Color.RED, 2),),  # トップ赤5 に色一致で出せる
        top=card("5", Color.RED, 100),
    )
    out = apply(st, DrawAction("p1"))
    assert out.is_draw is False
    assert out.current_player == "p2"  # 通常の手番送り


def test_no_stalemate_when_deck_refillable():
    """捨て山にトップ以外が残る＝再シャッフルで引ける → 行き詰まりでない。"""
    st = _state(
        p1=(card("9", Color.GREEN, 1),),
        p2=(card("8", Color.BLUE, 2),),
        top=(card("7", Color.YELLOW, 101), card("5", Color.RED, 100)),  # トップ以外に1枚
    )
    out = apply(st, DrawAction("p1"))
    assert out.is_draw is False
    assert len(out.hands["p1"]) == 2  # 再シャッフルして1枚引けた


def test_wild_holder_is_not_stalemate():
    """ワイルドを持つ側は常に出せる（上がり制限撤廃）→ 引き分けにならない。"""
    st = _state(
        p1=(card("9", Color.GREEN, 1),),
        p2=(card("wild", None, 2),),
        top=card("5", Color.RED, 100),
    )
    out = apply(st, DrawAction("p1"))
    assert out.is_draw is False


def test_forced_draw_with_cards_remaining_is_not_stalemate():
    """強制ドロー後に山札が残る（補充可）なら、一時的に両者不出でも引き分けにしない（#74）。

    ``_deck_unrefillable`` ガードが load-bearing であることを固定する回帰テスト。この
    ガードを常時 True 化すると「山に札が残るのに引き分け」の誤検出になる（両者不出の
    on_turn_end 経路をこのテストが実際に通す）。
    """
    st = _state(
        p1=(card("8", Color.BLUE, 1),),  # 赤draw2 に出せない
        p2=(card("9", Color.GREEN, 2),),  # 出せない
        top=card("draw2", Color.RED, 100),
        draw=(  # 山に4枚（いずれも赤draw2 に出せない）
            card("1", Color.GREEN, 7),
            card("2", Color.YELLOW, 8),
            card("3", Color.GREEN, 9),
            card("4", Color.YELLOW, 10),
        ),
        current="p2",
        pending=2,
        awaiting={"p2": ("draw",)},
    )
    out = apply(st, DrawAction("p2"))
    assert out.is_draw is False  # 山に札が残る＝行き詰まりでない（誤検出しない）
    assert len(out.draw_pile) == 2  # 強制2枚引いて2枚残
    assert len(out.hands["p2"]) == 3


def test_forced_draw_forgives_unpayable_then_draws_on_dead_deck():
    """強制ドローが山切れで払いきれない時は引ける分だけ引き（残り免除）、両者不出なら引き分け。"""
    st = _state(
        p1=(card("9", Color.GREEN, 1),),
        p2=(card("8", Color.BLUE, 2),),
        top=card("draw2", Color.RED, 100),
        draw=(card("1", Color.YELLOW, 7),),  # 山は1枚のみ（誰も出せない札）
        current="p2",
        pending=4,
        awaiting={"p2": ("draw",)},
    )
    out = apply(st, DrawAction("p2"))
    assert len(out.hands["p2"]) == 2  # 1枚だけ引けた（残り3枚は免除）
    assert out.pending_draw == 0
    assert out.is_draw is True  # 引いた後も両者不出・山切れ → 引き分け
