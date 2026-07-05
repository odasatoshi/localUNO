"""last_event（カットイン演出用の一時イベント）の試験（#97）。

サーバ権威で「今のアクションで何が起きたか」を1件だけ運び、次アクションでクリアされる
ことを、active な ENABLED_RULES 経由で検証する。フロントのカットインはこの last_event に
依存する（誰が宣言/指摘/強制ドローしたか）。
"""

from __future__ import annotations

import random

from lUNO.engine.actions import (
    ChallengeUnoAction,
    DeclareUnoAction,
    DrawAction,
)
from lUNO.engine.cards import DRAW2, CardInstance, CardType, Color
from lUNO.engine.engine import apply_action
from lUNO.engine.state import GameEvent, GameState, player_view
from lUNO.rules import registry


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def apply(state, action):
    return apply_action(registry(), state, action)


def _state(p1, p2, current="p1", draw=(), pending=0, awaiting=None):
    return GameState(
        hands={"p1": p1, "p2": p2},
        draw_pile=draw,
        discard_pile=(card("5", Color.RED, 90),),
        current_player=current,
        rng_state=random.Random(0).getstate(),
        pending_draw=pending,
        awaiting=awaiting or {current: ("play", "draw")},
    )


def test_valid_declare_sets_uno_event():
    st = _state(p1=(card("7", Color.RED, 1),), p2=(card("9", Color.GREEN, 2),))
    out = apply(st, DeclareUnoAction("p1"))
    assert out.last_event == GameEvent("uno", by="p1")


def test_misdeclare_sets_uno_misfire_event():
    st = _state(
        p1=(card("7", Color.RED, 1), card("3", Color.RED, 2)),  # 2枚＝誤宣言
        p2=(card("9", Color.GREEN, 3),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    out = apply(st, DeclareUnoAction("p1"))
    assert out.last_event.kind == "uno_misfire"
    assert out.last_event.target == "p1"
    assert out.last_event.amount == 2


def test_challenge_success_sets_event():
    st = _state(
        p1=(card("7", Color.RED, 1),),  # 1枚・未宣言
        p2=(card("9", Color.GREEN, 2),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    out = apply(st, ChallengeUnoAction("p2"))
    assert out.last_event.kind == "challenge_success"
    assert out.last_event.by == "p2"
    assert out.last_event.target == "p1"
    assert out.last_event.amount == 2


def test_challenge_misfire_sets_event():
    st = _state(
        p1=(card("7", Color.RED, 1), card("8", Color.RED, 4)),  # 2枚＝指摘は誤爆
        p2=(card("9", Color.GREEN, 2),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    out = apply(st, ChallengeUnoAction("p2"))
    assert out.last_event.kind == "challenge_misfire"
    assert out.last_event.by == "p2"
    assert out.last_event.target == "p2"  # 押した本人が引く


def test_forced_draw_sets_event():
    st = _state(
        p1=(card("9", Color.GREEN, 1),),
        p2=(card("8", Color.BLUE, 2),),
        current="p2",
        pending=2,
        draw=(card("1", Color.YELLOW, 6), card("3", Color.YELLOW, 7)),
        awaiting={"p2": ("draw",)},
    )
    out = apply(st, DrawAction("p2"))
    assert out.last_event == GameEvent("forced_draw", target="p2", amount=2)


def test_voluntary_draw_has_no_event():
    st = _state(
        p1=(card("9", Color.GREEN, 1),),
        p2=(card("8", Color.BLUE, 2),),
        draw=(card("1", Color.YELLOW, 6),),
    )
    out = apply(st, DrawAction("p1"))
    assert out.last_event is None  # 自主ドローは強制ドローでないのでイベントなし


def test_event_cleared_on_next_action():
    # 宣言でイベントが立つ → 次のアクション（自主ドロー）でクリアされる
    st = _state(
        p1=(card("7", Color.RED, 1),),
        p2=(card("9", Color.GREEN, 2),),
        draw=(card("1", Color.BLUE, 6),),
    )
    after_declare = apply(st, DeclareUnoAction("p1"))
    assert after_declare.last_event is not None
    after_draw = apply(after_declare, DrawAction("p1"))
    assert after_draw.last_event is None


def test_player_view_exposes_last_event():
    st = _state(p1=(card("7", Color.RED, 1),), p2=(card("9", Color.GREEN, 2),))
    st = st.with_last_event(GameEvent("challenge_success", by="p2", target="p1", amount=2))
    d = player_view(st, "p1").to_dict()
    assert d["last_event"] == {"kind": "challenge_success", "by": "p2", "target": "p1", "amount": 2}
    # イベント無しは None
    assert player_view(_state(p1=(), p2=()), "p1").to_dict()["last_event"] is None


def test_forced_draw_via_draw2_play_flow():
    """Draw2 を出す→相手が強制ドロー、の実フローで forced_draw イベントが載る。"""
    from lUNO.engine.actions import PlayAction

    st = _state(
        p1=(card(DRAW2, Color.RED, 1), card("5", Color.RED, 9)),
        p2=(card("9", Color.GREEN, 3),),
        draw=(card("0", Color.RED, 6), card("1", Color.BLUE, 7)),
    )
    after = apply(st, PlayAction("p1", (1,)))  # Draw2 → p2 に pending2
    assert after.last_event is None  # 出した時点ではまだドローしていない
    drawn = apply(after, DrawAction("p2"))  # p2 が強制ドロー
    assert drawn.last_event == GameEvent("forced_draw", target="p2", amount=2)
