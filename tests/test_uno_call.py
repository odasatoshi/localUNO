"""ローカルルール #41 UNO 宣言＋指摘の試験（docs/house-rules.md §6）。

UNO! / UNO言ってない! は engine の常時受理割り込み。本ルールが宣言の有効性・指摘の
成否・ペナルティ・フラグ整理を担う。active な ENABLED_RULES で検証する。
"""

from __future__ import annotations

import random

from lUNO.engine.actions import ChallengeUnoAction, DeclareUnoAction, DrawAction, PlayAction
from lUNO.engine.cards import SKIP, CardInstance, CardType, Color
from lUNO.engine.state import GameState
from lUNO.rules import registry


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def _state(p1, p2, current="p1", draw=(), awaiting=None):
    return GameState(
        hands={"p1": p1, "p2": p2},
        draw_pile=draw,
        discard_pile=(card("3", Color.RED, 90),),
        current_player=current,
        rng_state=random.Random(0).getstate(),
        awaiting=awaiting or {current: ("play", "draw")},
    )


def test_declare_uno_at_one_card_sets_flag():
    """手札1枚で「UNO!」を押すと宣言済みになる。"""
    st = _state(p1=(card("7", Color.RED, 1),), p2=(card("9", Color.GREEN, 2),))
    out = apply(st, DeclareUnoAction("p1"))
    assert "p1" in out.uno_declared


def test_declare_uno_at_two_cards_penalizes_declarer():
    """手札2枚での「UNO!」は誤宣言＝本人が2枚引く（宣言済みにもならない, #79）。"""
    st = _state(
        p1=(card("7", Color.RED, 1), card("5", Color.RED, 2)),
        p2=(card("9", Color.GREEN, 3),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    out = apply(st, DeclareUnoAction("p1"))
    assert len(out.hands["p1"]) == 2 + 2  # 誤宣言で本人が2枚引く
    assert "p1" not in out.uno_declared  # 宣言は成立しない
    assert len(out.hands["p2"]) == 1  # 相手は不変


def test_declare_uno_at_two_cards_off_turn_also_penalizes():
    """手番外の誤宣言も本人が2枚引く（常時受理・#79）。"""
    st = _state(
        p1=(card("9", Color.GREEN, 2),),
        p2=(card("7", Color.RED, 1), card("5", Color.RED, 2)),  # 手番外・2枚
        current="p1",
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    out = apply(st, DeclareUnoAction("p2"))
    assert len(out.hands["p2"]) == 2 + 2  # 手番外でも誤宣言ペナルティ
    assert "p2" not in out.uno_declared


def test_declare_uno_redeclare_when_already_declared_is_noop():
    """1枚・宣言済みでの再宣言は no-op（正当な状態の再送・ペナルティなし, #79）。"""
    st = _state(
        p1=(card("7", Color.RED, 1),),  # 1枚・宣言済み
        p2=(card("9", Color.GREEN, 3),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    ).replace(uno_declared=frozenset({"p1"}))
    out = apply(st, DeclareUnoAction("p1"))
    assert "p1" in out.uno_declared
    assert len(out.hands["p1"]) == 1  # ペナルティなし


def test_declare_uno_ignored_after_win():
    """終局後（上がり）の宣言は無効＝ペナルティも成立もなし（#79）。手札0枚を含む。"""
    st = _state(
        p1=(),  # 上がり（0枚）
        p2=(card("9", Color.GREEN, 3),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    ).replace(winner="p1")
    out = apply(st, DeclareUnoAction("p1"))
    assert len(out.hands["p1"]) == 0  # 誰も引かない
    assert "p1" not in out.uno_declared


def test_declare_uno_at_two_cards_after_win_is_noop():
    """終局後は手札2枚以上でも誤宣言ペナルティが出ない（枚数に依らず無効, #79）。"""
    st = _state(
        p1=(card("7", Color.RED, 1), card("5", Color.RED, 2)),  # 2枚
        p2=(),  # 上がり
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    ).replace(winner="p2")
    out = apply(st, DeclareUnoAction("p1"))
    assert len(out.hands["p1"]) == 2  # 終局後はペナルティなし


def test_challenge_success_penalizes_undeclared_opponent():
    """相手が1枚かつ未宣言なら指摘成功、相手が2枚引く。"""
    st = _state(
        p1=(card("9", Color.GREEN, 2), card("5", Color.RED, 5)),  # 指摘する側
        p2=(card("7", Color.RED, 1),),  # 1枚・未宣言
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    out = apply(st, ChallengeUnoAction("p1"))
    assert len(out.hands["p2"]) == 1 + 2  # 相手が2枚引く
    assert len(out.hands["p1"]) == 2  # 指摘者は不変


def test_challenge_misfire_when_target_not_at_one():
    """相手が1枚でないのに指摘＝誤爆、押した本人が2枚引く。"""
    st = _state(
        p1=(card("9", Color.GREEN, 2),),  # 指摘する側
        p2=(card("7", Color.RED, 1), card("5", Color.RED, 5)),  # 2枚
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    out = apply(st, ChallengeUnoAction("p1"))
    assert len(out.hands["p1"]) == 1 + 2  # 押した本人が2枚引く
    assert len(out.hands["p2"]) == 2


def test_challenge_misfire_when_target_already_declared():
    """相手が既に「UNO!」済み（宣言後）の指摘＝誤爆、本人が2枚引く。"""
    st = _state(
        p1=(card("9", Color.GREEN, 2),),
        p2=(card("7", Color.RED, 1),),  # 1枚だが宣言済み
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    ).replace(uno_declared=frozenset({"p2"}))
    out = apply(st, ChallengeUnoAction("p1"))
    assert len(out.hands["p1"]) == 1 + 2  # 誤爆で本人が2枚
    assert len(out.hands["p2"]) == 1


def test_declared_flag_cleared_when_hand_grows():
    """宣言済みでも手札が1枚でなくなれば（ドロー等）フラグは解除される。"""
    st = _state(
        p1=(card("7", Color.RED, 1),),  # 1枚・宣言済み
        p2=(card("9", Color.GREEN, 3),),
        draw=(card("1", Color.BLUE, 6),),
    ).replace(uno_declared=frozenset({"p1"}))
    out = apply(st, DrawAction("p1"))  # 引いて2枚に
    assert "p1" not in out.uno_declared


def test_challenge_ignored_after_win():
    """終局後の指摘は無効（ペナルティを課さない）。"""
    st = _state(
        p1=(card("9", Color.GREEN, 2),),
        p2=(card("7", Color.RED, 1),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    ).replace(winner="p1")
    out = apply(st, ChallengeUnoAction("p2"))
    assert len(out.hands["p1"]) == 1
    assert len(out.hands["p2"]) == 1  # 誰も引かない


def test_challenge_and_declare_ignored_after_draw():
    """山切れ引き分け（is_draw）後は指摘も宣言も無効（winner と対称, #74）。"""
    st = _state(
        p1=(card("9", Color.GREEN, 2),),
        p2=(card("7", Color.RED, 1),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    ).replace(is_draw=True)
    # 指摘: 相手が1枚でも終局後なのでペナルティなし
    out = apply(st, ChallengeUnoAction("p2"))
    assert len(out.hands["p1"]) == 1
    assert len(out.hands["p2"]) == 1
    # 宣言: 1枚でも終局後なのでフラグを立てない
    out2 = apply(st, DeclareUnoAction("p1"))
    assert "p1" not in out2.uno_declared


# --- 結合（実アクション列での往復） ----------------------------------------


def test_declare_then_challenge_is_misfire_via_actions():
    """アクション列: 1枚で UNO! 宣言 → 相手が指摘 = 誤爆（本人が2枚）。"""
    st = _state(
        p1=(card("7", Color.RED, 1),),  # 1枚
        p2=(card("9", Color.GREEN, 2),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    st = apply(st, DeclareUnoAction("p1"))
    assert "p1" in st.uno_declared
    out = apply(st, ChallengeUnoAction("p2"))  # 宣言済みへの指摘＝誤爆
    assert len(out.hands["p2"]) == 1 + 2  # 押した p2 が2枚
    assert len(out.hands["p1"]) == 1


def test_skip_to_one_card_undeclared_is_catchable():
    """スキップで一時的に1枚になり未宣言なら指摘成功（§6「スキップで1枚は必要」）。"""
    st = _state(
        p1=(card(SKIP, Color.RED, 1), card("5", Color.RED, 2)),
        p2=(card("9", Color.GREEN, 3),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    st = apply(st, PlayAction("p1", (1,)))  # スキップ→p1 は1枚・手番継続
    assert len(st.hands["p1"]) == 1
    assert "p1" not in st.uno_declared  # 未宣言
    out = apply(st, ChallengeUnoAction("p2"))  # 指摘成功
    assert len(out.hands["p1"]) == 1 + 2


def test_win_two_to_zero_then_challenge_noop():
    """複数枚出しで 2→0 一気上がり後は challenge が no-op（§6 宣言不要）。"""
    st = _state(
        p1=(card("7", Color.RED, 1), card("7", Color.BLUE, 2)),
        p2=(card("9", Color.GREEN, 3),),
        draw=(card("1", Color.BLUE, 6), card("2", Color.YELLOW, 7)),
    )
    won = apply(st, PlayAction("p1", (1, 2)))  # 2枚出して上がり（top 赤3に赤7が合致）
    assert won.winner == "p1"
    out = apply(won, ChallengeUnoAction("p2"))
    assert len(out.hands["p2"]) == 1  # 終局後の指摘は無効＝ペナルティなし


def apply(state, action):
    from lUNO.engine.engine import apply_action

    return apply_action(registry(), state, action)
