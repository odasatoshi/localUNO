"""ローカルルール #37 複数枚出しの試験（docs/house-rules.md §2）。

standard + ...（active な ENABLED_RULES）で複数枚出しの合法性・効果を検証する。
engine（#35）の card_ids/played_cards/can_stack の上に rules/ だけで載る。
"""

from __future__ import annotations

import random

import pytest

from lUNO.engine.actions import ChooseColorAction, PlayAction
from lUNO.engine.cards import DRAW2, SKIP, WILD, CardInstance, CardType, Color
from lUNO.engine.engine import STANDARD_TURN_ACTIONS, IllegalAction, apply_action
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
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )


def test_multi_same_number():
    """同じ数字を複数枚出せる。出した順に積み最後がトップ。"""
    st = _state(
        p1=(card("7", Color.RED, 1), card("7", Color.BLUE, 2), card("9", Color.GREEN, 9)),
        p2=(card("4", Color.GREEN, 3),),
        top=card("3", Color.RED, 4),  # 赤7が色一致で先頭合法
    )
    out = apply_action(registry(), st, PlayAction("p1", (1, 2)))
    assert [c.id for c in out.discard_pile[-2:]] == [1, 2]
    assert out.discard_pile[-1].id == 2  # 最後がトップ
    assert len(out.hands["p1"]) == 1
    assert out.current_player == "p2"


def test_multi_skip_effect_once():
    """同じ記号（スキップ）を複数出せるが効果は1回（自分の手番継続）。"""
    st = _state(
        p1=(card(SKIP, Color.RED, 1), card(SKIP, Color.BLUE, 2), card("9", Color.GREEN, 9)),
        p2=(card("4", Color.GREEN, 3),),
        top=card(SKIP, Color.RED, 4),
    )
    out = apply_action(registry(), st, PlayAction("p1", (1, 2)))
    assert out.current_player == "p1"  # スキップ効果は1回＝自分の手番継続
    assert out.awaiting == {"p1": STANDARD_TURN_ACTIONS}


def test_multi_mixed_symbol_rejected():
    """数字と記号の混在は不可（同記号でない群は can_stack で却下）。"""
    st = _state(
        p1=(card("7", Color.RED, 1), card(SKIP, Color.RED, 2)),
        p2=(card("4", Color.GREEN, 3),),
        top=card("3", Color.RED, 4),
    )
    with pytest.raises(IllegalAction):
        apply_action(registry(), st, PlayAction("p1", (1, 2)))


def test_multi_draw2_accumulates():
    """Draw2 を複数枚出すと枚数分累積する（§2: Draw2 のみ累積）。"""
    st = _state(
        p1=(card(DRAW2, Color.RED, 1), card(DRAW2, Color.BLUE, 2), card("9", Color.GREEN, 9)),
        p2=(card("4", Color.GREEN, 3),),
        top=card("7", Color.RED, 4),
    )
    out = apply_action(registry(), st, PlayAction("p1", (1, 2)))
    assert out.pending_draw == 4  # 2枚 × 2
    assert out.current_player == "p2"
    assert set(out.awaiting["p2"]) == {"draw", "play"}  # draw2_stack で返せる


def test_multi_wild_choose_color_once():
    """ワイルドを複数出せる。色指定は最後の1回だけ。"""
    st = _state(
        p1=(card(WILD, None, 1), card(WILD, None, 2), card("9", Color.GREEN, 9)),
        p2=(card("4", Color.GREEN, 3),),
        top=card("7", Color.RED, 4),
    )
    paused = apply_action(registry(), st, PlayAction("p1", (1, 2)))
    assert paused.awaiting == {"p1": ("choose_color",)}  # 色選択は1回
    done = apply_action(registry(), paused, ChooseColorAction("p1", Color.BLUE))
    assert done.forced_color == Color.BLUE
    assert done.current_player == "p2"


def test_single_card_play_still_works():
    """単数出しは従来どおり（複数枚ルール導入による回帰なし）。"""
    st = _state(
        p1=(card("7", Color.RED, 1), card("9", Color.GREEN, 9)),
        p2=(card("4", Color.GREEN, 3),),
        top=card("3", Color.RED, 4),
    )
    out = apply_action(registry(), st, PlayAction("p1", (1,)))
    assert out.discard_pile[-1].id == 1
    assert out.current_player == "p2"


def test_multi_play_lead_must_be_playable():
    """先頭カードが場に合法でなければ複数出しごと却下（can_play は先頭を見る）。"""
    st = _state(
        p1=(card("9", Color.GREEN, 1), card("9", Color.YELLOW, 2)),
        p2=(card("4", Color.GREEN, 3),),
        top=card("3", Color.RED, 4),  # 緑9は赤3に色/数字で不一致
    )
    with pytest.raises(IllegalAction):
        apply_action(registry(), st, PlayAction("p1", (1, 2)))
