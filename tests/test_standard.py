"""rules/standard.py + 有効化リストの試験（issue #11 の完了条件を担保）。

- 標準ルールのみで二人対戦が一巡できる（配札→出す/引く→効果→上がり）
- ドロー2/ワイルドドロー4/色選択が仕様どおり
- engine を改修せず rules/ 内で完結（rules.registry() で組んだ実行器を engine に渡す）
"""

from __future__ import annotations

import random

import pytest

from lUNO.engine.actions import ChooseColorAction, DrawAction, PlayAction
from lUNO.engine.cards import (
    DRAW2,
    DRAW4,
    REVERSE,
    SKIP,
    WILD,
    CardInstance,
    CardType,
    Color,
)
from lUNO.engine.engine import IllegalAction, apply_action
from lUNO.engine.hooks import Ctx
from lUNO.engine.state import GameState
from lUNO.rules import ENABLED_RULES, registry, setup_game, standard

P = ("p1", "p2")


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def state_with(*, p1, p2, top, draw=()) -> GameState:
    return GameState(
        hands={"p1": p1, "p2": p2},
        draw_pile=draw,
        discard_pile=(top,),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": ("play", "draw")},
    )


# --- 有効化リスト・セットアップ ---------------------------------------------


def test_enabled_rules_standard_first():
    assert ENABLED_RULES[0] is standard.RULES


def test_setup_game_flips_non_wild_top():
    st = setup_game(P, seed=3)
    assert len(st.hands["p1"]) == 7
    assert len(st.hands["p2"]) == 7
    assert st.discard_pile and not st.discard_pile[-1].is_wild
    total = (
        len(st.hands["p1"]) + len(st.hands["p2"]) + len(st.draw_pile) + len(st.discard_pile)
    )
    assert total == 108
    assert st.current_player == "p1"
    assert st.awaiting == {"p1": ("play", "draw")}


def test_setup_game_deterministic():
    assert setup_game(P, seed=5) == setup_game(P, seed=5)


# --- can_play（色/数字/記号/ワイルド/強制色/制限） --------------------------


def _can_play(reg, st, played, owner="p1"):
    return reg.can_play(Ctx.from_state(st, card=played, owner=owner))


def test_can_play_color_number_symbol_and_wild():
    reg = registry()
    st = state_with(p1=(), p2=(), top=card("7", Color.RED, 100))
    assert _can_play(reg, st, card("3", Color.RED, 1)) is True  # 色一致
    assert _can_play(reg, st, card("7", Color.BLUE, 2)) is True  # 記号(数字)一致
    assert _can_play(reg, st, card("3", Color.BLUE, 3)) is False  # 不一致
    assert _can_play(reg, st, card(WILD, None, 4)) is True  # ワイルドは常に可


def test_can_play_respects_forced_color():
    reg = registry()
    st = state_with(p1=(), p2=(), top=card(WILD, None, 100)).with_forced_color(Color.RED)
    assert _can_play(reg, st, card("3", Color.RED, 1)) is True
    assert _can_play(reg, st, card("3", Color.BLUE, 2)) is False


def test_cannot_win_on_wild_restriction():
    reg = registry()
    st = state_with(
        p1=(card(WILD, None, 1),),  # 最後の1枚がワイルド
        p2=(card("9", Color.GREEN, 2),),
        top=card("7", Color.RED, 3),
    )
    assert _can_play(reg, st, card(WILD, None, 1)) is False
    with pytest.raises(IllegalAction):
        apply_action(reg, st, PlayAction("p1", 1))


# --- 効果 -------------------------------------------------------------------


def test_number_card_passes_turn():
    reg = registry()
    st = state_with(
        p1=(card("5", Color.RED, 1), card("2", Color.RED, 2)),
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 4),
    )
    out = apply_action(reg, st, PlayAction("p1", 1))
    assert out.current_player == "p2"
    assert out.awaiting == {"p2": ("play", "draw")}


def test_skip_keeps_turn_with_actor():
    reg = registry()
    st = state_with(
        p1=(card(SKIP, Color.RED, 1), card("2", Color.RED, 2)),
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 4),
    )
    out = apply_action(reg, st, PlayAction("p1", 1))
    assert out.current_player == "p1"  # 2人では相手を飛ばす＝自分の手番
    assert out.awaiting == {"p1": ("play", "draw")}


def test_reverse_acts_as_skip_in_two_player():
    reg = registry()
    st = state_with(
        p1=(card(REVERSE, Color.RED, 1), card("2", Color.RED, 2)),
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 4),
    )
    out = apply_action(reg, st, PlayAction("p1", 1))
    assert out.current_player == "p1"


def test_draw2_forces_opponent_draw_then_returns_turn():
    reg = registry()
    st = state_with(
        p1=(card(DRAW2, Color.RED, 1), card("5", Color.BLUE, 2)),
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 4),
        draw=(card("1", Color.BLUE, 5), card("2", Color.YELLOW, 6)),
    )
    after = apply_action(reg, st, PlayAction("p1", 1))
    assert after.pending_draw == 2
    assert after.current_player == "p2"
    assert after.awaiting == {"p2": ("draw",)}  # 相手は引くのみ

    drawn = apply_action(reg, after, DrawAction("p2"))
    assert len(drawn.hands["p2"]) == 1 + 2  # 2枚引いた
    assert drawn.pending_draw == 0
    assert drawn.current_player == "p1"  # 手番が戻る（2人での skip 相当）


def test_wild_choose_color_then_pass_turn():
    reg = registry()
    st = state_with(
        p1=(card(WILD, None, 1), card("5", Color.RED, 2)),
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 4),
    )
    paused = apply_action(reg, st, PlayAction("p1", 1))
    assert paused.awaiting == {"p1": ("choose_color",)}
    assert paused.current_player == "p1"
    assert paused.pending_draw == 0  # 通常ワイルドは強制ドロー無し

    done = apply_action(reg, paused, ChooseColorAction("p1", Color.BLUE))
    assert done.forced_color == Color.BLUE
    assert done.current_player == "p2"
    assert done.awaiting == {"p2": ("play", "draw")}


def test_wild_draw4_choose_then_opponent_draws4():
    reg = registry()
    draw = tuple(card(str(i), Color.BLUE, 10 + i) for i in range(4))
    st = state_with(
        p1=(card(DRAW4, None, 1), card("5", Color.RED, 2)),
        p2=(card("9", Color.GREEN, 3),),
        top=card("7", Color.RED, 4),
        draw=draw,
    )
    paused = apply_action(reg, st, PlayAction("p1", 1))
    assert paused.awaiting == {"p1": ("choose_color",)}
    assert paused.pending_draw == 4

    done = apply_action(reg, paused, ChooseColorAction("p1", Color.GREEN))
    assert done.forced_color == Color.GREEN
    assert done.current_player == "p2"
    assert done.awaiting == {"p2": ("draw",)}

    drew = apply_action(reg, done, DrawAction("p2"))
    assert len(drew.hands["p2"]) == 1 + 4
    assert drew.pending_draw == 0
    assert drew.current_player == "p1"
    assert drew.forced_color == Color.GREEN  # 強制色は維持


# --- 上がり・得点 -----------------------------------------------------------


def test_play_last_card_wins():
    reg = registry()
    st = state_with(
        p1=(card("5", Color.RED, 1),),
        p2=(card("9", Color.GREEN, 2),),
        top=card("7", Color.RED, 3),
    )
    out = apply_action(reg, st, PlayAction("p1", 1))
    assert out.winner == "p1"
    assert out.current_player == "p1"  # 終局で手番送りしない


def test_win_on_skip_closes_awaiting():
    """スキップを最後の1枚で上がると終局が閉じ、その後の行動は受理されない。"""
    reg = registry()
    st = state_with(
        p1=(card(SKIP, Color.RED, 1),),
        p2=(card("9", Color.GREEN, 2),),
        top=card("7", Color.RED, 3),
    )
    out = apply_action(reg, st, PlayAction("p1", 1))
    assert out.winner == "p1"
    assert out.awaiting == {}  # 終局: 受理集合を空に
    assert out.current_player == "p1"
    with pytest.raises(IllegalAction):
        apply_action(reg, out, DrawAction("p1"))


def test_win_on_draw2_closes_awaiting_and_pending():
    """ドロー2を最後の1枚で上がると、相手への強制ドローも残さず終局する。"""
    reg = registry()
    st = state_with(
        p1=(card(DRAW2, Color.RED, 1),),
        p2=(card("9", Color.GREEN, 2),),
        top=card("7", Color.RED, 3),
        draw=(card("1", Color.BLUE, 4), card("2", Color.BLUE, 5)),
    )
    out = apply_action(reg, st, PlayAction("p1", 1))
    assert out.winner == "p1"
    assert out.awaiting == {}
    assert out.pending_draw == 0
    with pytest.raises(IllegalAction):
        apply_action(reg, out, DrawAction("p2"))  # 終局後は相手も引けない


def test_standard_score_counts_loser_hand():
    reg = registry()
    st = state_with(
        p1=(),
        p2=(card("5", Color.RED, 1), card(SKIP, Color.BLUE, 2), card(WILD, None, 3)),
        top=card("7", Color.RED, 4),
    ).with_winner("p1")
    assert reg.score(Ctx.from_state(st, owner="p1")) == 5 + 20 + 50


# --- 一巡（配札→出す→引く→効果→上がり） -----------------------------------


def test_full_round_play_draw_effect_win():
    reg = registry()
    st = state_with(
        p1=(card("5", Color.RED, 1), card("2", Color.RED, 2)),
        p2=(card("9", Color.GREEN, 3), card("3", Color.RED, 4)),
        top=card("7", Color.RED, 5),
        draw=(card("0", Color.RED, 6),),
    )
    # p1: 赤5 を出す → 相手へ
    st = apply_action(reg, st, PlayAction("p1", 1))
    assert st.current_player == "p2"
    # p2: 緑9 は赤5 に出せない → 引く（配布から赤0）→ 相手へ
    st = apply_action(reg, st, DrawAction("p2"))
    assert st.current_player == "p1"
    assert len(st.hands["p2"]) == 3
    # p1: 赤2 を出す → 手札が尽きて上がり
    st = apply_action(reg, st, PlayAction("p1", 2))
    assert st.winner == "p1"
