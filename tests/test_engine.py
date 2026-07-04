"""engine/engine.py の試験（spec.md §2/§3.3/§3.6 の完了条件を担保）。

- 同シード＋同 Action 列 → 同 GameState（決定性）
- awaiting 不整合な Action を拒否
- ワイルド→色選択待ち→確定 の継続（応答待ちライフサイクル）

エンジンはルール非依存なので、効果はインラインのルール雛形で与えて駆動する。
"""

from __future__ import annotations

import random

import pytest

from lUNO.engine.actions import (
    ChallengeUnoAction,
    ChooseColorAction,
    DeclareUnoAction,
    DrawAction,
    PlayAction,
    ResetAction,
)
from lUNO.engine.cards import WILD, CardInstance, CardType, Color
from lUNO.engine.engine import (
    ON_CHALLENGE_UNO,
    ON_DECLARE_UNO,
    STANDARD_TURN_ACTIONS,
    IllegalAction,
    apply_action,
    apply_actions,
)
from lUNO.engine.hooks import (
    CAN_PLAY,
    CAN_STACK,
    ON_AFTER_PLAY,
    ON_CHOOSE_COLOR,
    ON_TURN_END,
    build_registry,
)
from lUNO.engine.state import GameState

P = ("p1", "p2")
PERMISSIVE = {CAN_PLAY: lambda current, ctx: True}


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


# --- 決定性（同シード＋同 Action 列 → 同 GameState） -------------------------


def test_determinism_same_seed_same_actions():
    reg = build_registry([])
    acts = [DrawAction("p1"), DrawAction("p2"), DrawAction("p1"), DrawAction("p2")]
    a = apply_actions(reg, GameState.new_game(P, 5), acts)
    b = apply_actions(reg, GameState.new_game(P, 5), acts)
    assert a == b
    assert a.rng_state == b.rng_state


def test_determinism_differs_by_seed():
    reg = build_registry([])
    acts = [DrawAction("p1"), DrawAction("p2")]
    a = apply_actions(reg, GameState.new_game(P, 1), acts)
    b = apply_actions(reg, GameState.new_game(P, 2), acts)
    assert a != b


# --- awaiting 不整合な Action の拒否（§3.6） --------------------------------


def test_reject_action_from_non_current_player():
    reg = build_registry([])
    st = GameState.new_game(P, 1)  # awaiting = {p1: [play, draw]}
    with pytest.raises(IllegalAction):
        apply_action(reg, st, DrawAction("p2"))


def test_reject_action_type_not_allowed():
    reg = build_registry([])
    st = GameState.new_game(P, 1)
    with pytest.raises(IllegalAction):
        apply_action(reg, st, ChooseColorAction("p1", Color.RED))  # 手番だが choose_color 不可


# --- play の機構と can_play 判定（§3.3） ------------------------------------


def test_play_moves_card_and_passes_turn():
    reg = build_registry([PERMISSIVE])
    st = GameState.new_game(P, 1)
    played = st.hands["p1"][0]
    out = apply_action(reg, st, PlayAction("p1", (played.id,)))
    assert played not in out.hands["p1"]
    assert len(out.hands["p1"]) == 6
    assert out.discard_pile[-1] == played  # 捨て山トップに乗る
    assert out.current_player == "p2"  # 手番が相手へ
    assert out.awaiting == {"p2": STANDARD_TURN_ACTIONS}


def test_play_rejected_when_can_play_false():
    reg = build_registry([])  # can_play シード False・許可ルール無し
    st = GameState.new_game(P, 1)
    played = st.hands["p1"][0]
    with pytest.raises(IllegalAction):
        apply_action(reg, st, PlayAction("p1", (played.id,)))


def test_play_card_not_in_hand_rejected():
    reg = build_registry([PERMISSIVE])
    st = GameState.new_game(P, 1)
    with pytest.raises(IllegalAction):
        apply_action(reg, st, PlayAction("p1", (99999,)))


# --- 応答待ちライフサイクル: ワイルド→色選択待ち→確定（§3.6） ---------------


def _wild_effect(state, ctx):
    """ワイルドを出したら本人の色選択を待って停止する（rule-authoring 例）。"""
    if ctx.card is not None and ctx.card.is_wild:
        return state.with_awaiting({ctx.current_player: (ChooseColorAction.type,)})
    return state


def _wild_state() -> GameState:
    wild = card(WILD, None, 1)
    return GameState(
        hands={"p1": (wild, card("5", Color.RED, 2)), "p2": (card("9", Color.GREEN, 4),)},
        draw_pile=(),
        discard_pile=(card("3", Color.BLUE, 3),),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )


def _set_forced_color(state, ctx):
    """継続フック: 強制色を確定する（永続フィールド書き換えは rules の責務, §3.2）。"""
    return state.with_forced_color(ctx.action.color)


def test_wild_pauses_for_color_then_continues():
    reg = build_registry(
        [PERMISSIVE, {ON_AFTER_PLAY: _wild_effect}, {ON_CHOOSE_COLOR: _set_forced_color}]
    )
    st = _wild_state()

    # play(wild): 効果が色選択待ちを立てて停止（手番送りしない）
    paused = apply_action(reg, st, PlayAction("p1", (1,)))
    assert paused.discard_pile[-1].id == 1
    assert paused.awaiting == {"p1": ("choose_color",)}
    assert paused.current_player == "p1"

    # 停止中は play/draw は受理されない（choose_color のみ）
    with pytest.raises(IllegalAction):
        apply_action(reg, paused, DrawAction("p1"))

    # choose_color: 継続フックが強制色を確定し、手番を相手へ
    done = apply_action(reg, paused, ChooseColorAction("p1", Color.RED))
    assert done.forced_color == Color.RED
    assert done.current_player == "p2"
    assert done.awaiting == {"p2": STANDARD_TURN_ACTIONS}


# --- draw の機構（pending_draw・山切れ再シャッフル） ------------------------


def test_draw_one_and_pass_turn():
    reg = build_registry([])
    st = GameState.new_game(P, 1)
    before = len(st.hands["p1"])
    out = apply_action(reg, st, DrawAction("p1"))
    assert len(out.hands["p1"]) == before + 1
    assert out.current_player == "p2"


def test_draw_honors_pending_draw():
    reg = build_registry([])
    st = GameState.new_game(P, 1).with_pending_draw(3)
    before = len(st.hands["p1"])
    out = apply_action(reg, st, DrawAction("p1"))
    assert len(out.hands["p1"]) == before + 3
    assert out.pending_draw == 0


def test_draw_reshuffles_discard_deterministically():
    cards = tuple(card(str(i), Color.RED, i) for i in range(6))

    def mkstate() -> GameState:
        return GameState(
            hands={"p1": (), "p2": ()},
            draw_pile=(),
            discard_pile=cards,  # トップ = id 5
            current_player="p1",
            rng_state=random.Random(0).getstate(),
            awaiting={"p1": STANDARD_TURN_ACTIONS},
        )

    reg = build_registry([])
    a = apply_action(reg, mkstate(), DrawAction("p1"))
    b = apply_action(reg, mkstate(), DrawAction("p1"))
    assert a == b  # 再シャッフルも注入 RNG で決定的
    assert len(a.hands["p1"]) == 1  # 1枚引けた
    assert a.discard_pile[-1].id == 5  # 捨て山トップは温存


# --- reset（§8） ------------------------------------------------------------


def test_reset_reshuffles_and_deals_fresh():
    reg = build_registry([])
    st = apply_action(reg, GameState.new_game(P, 1), DrawAction("p1"))  # 手番は p2 側へ
    r = apply_action(reg, st, ResetAction("p1"))  # reset は常時受理
    assert len(r.hands["p1"]) == 7
    assert len(r.hands["p2"]) == 7
    assert len(r.draw_pile) == 108 - 14
    assert r.current_player == "p1"
    assert r.awaiting == {"p1": STANDARD_TURN_ACTIONS}


def test_reset_is_accepted_regardless_of_awaiting():
    reg = build_registry([])
    st = GameState.new_game(P, 1)  # awaiting = {p1: ...}
    # p2 からの reset も受理される（メタ操作）
    r = apply_action(reg, st, ResetAction("p2"))
    assert len(r.hands["p1"]) == 7


def test_reset_is_deterministic():
    reg = build_registry([])
    a = apply_action(reg, GameState.new_game(P, 7), ResetAction("p1"))
    b = apply_action(reg, GameState.new_game(P, 7), ResetAction("p1"))
    assert a == b


# --- 純粋性（入力 state を破壊しない） --------------------------------------


def test_apply_does_not_mutate_input_state():
    reg = build_registry([PERMISSIVE])
    st = GameState.new_game(P, 1)
    snapshot = (st.hands["p1"], dict(st.awaiting), st.rng_state, st.discard_pile)
    apply_action(reg, st, PlayAction("p1", (st.hands["p1"][0].id,)))
    assert (st.hands["p1"], dict(st.awaiting), st.rng_state, st.discard_pile) == snapshot


def test_draw_with_reshuffle_does_not_mutate_input_rng():
    """再シャッフルを伴う draw でも入力 state の rng_state は不変（決定性の根拠）。"""
    cards = tuple(card(str(i), Color.RED, i) for i in range(6))
    st = GameState(
        hands={"p1": (), "p2": ()},
        draw_pile=(),
        discard_pile=cards,
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )
    before_rng = st.rng_state
    apply_action(build_registry([]), st, DrawAction("p1"))
    assert st.rng_state == before_rng


# --- ターン終了フック・終局（§3.2/§3.6） ------------------------------------


def test_on_turn_end_fires_before_advance():
    log = []

    def mark(state, ctx):
        log.append(ctx.current_player)  # 手番送り前なので actor 文脈
        return state

    reg = build_registry([{ON_TURN_END: mark}])
    apply_action(reg, GameState.new_game(P, 1), DrawAction("p1"))
    assert log == ["p1"]


def test_winner_stops_turn_advance():
    """終局(winner)を立てると engine は手番送りしない（idle と terminal を区別）。"""

    def win_on_play(state, ctx):
        return state.with_winner(ctx.current_player)

    reg = build_registry([PERMISSIVE, {ON_AFTER_PLAY: win_on_play}])
    st = GameState.new_game(P, 1)
    out = apply_action(reg, st, PlayAction("p1", (st.hands["p1"][0].id,)))
    assert out.winner == "p1"
    assert out.current_player == "p1"  # 手番送りしない
    assert out.awaiting == {}  # 終局で誰も操作不可

    # 終局後は winner が PlayerView にも公開される
    from lUNO.engine.state import player_view

    assert player_view(out, "p2").winner == "p1"


# --- #35 基盤拡張: 複数枚出し（can_stack ゲート） ----------------------------


def _multi_state() -> GameState:
    """p1 が id 1,2 の2枚を持つ最小 state（top は id 3）。"""
    return GameState(
        hands={
            "p1": (card("7", Color.RED, 1), card("7", Color.BLUE, 2)),
            "p2": (card("9", Color.GREEN, 4),),
        },
        draw_pile=(),
        discard_pile=(card("3", Color.BLUE, 3),),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )


def test_multi_card_play_rejected_without_stack_rule():
    """既定では can_stack シード False → 複数枚出しは拒否（標準は単数のみ）。"""
    reg = build_registry([PERMISSIVE])  # can_play True だが can_stack ルール無し
    st = _multi_state()
    with pytest.raises(IllegalAction):
        apply_action(reg, st, PlayAction("p1", (1, 2)))


def test_multi_card_play_allowed_with_stack_rule():
    """can_stack を許可するルールがあれば複数枚を出せ、最後のカードがトップになる。"""
    reg = build_registry([PERMISSIVE, {CAN_STACK: lambda current, ctx: True}])
    st = _multi_state()
    out = apply_action(reg, st, PlayAction("p1", (1, 2)))
    assert len(out.hands["p1"]) == 0  # 2枚とも手札から抜ける
    assert [c.id for c in out.discard_pile[-2:]] == [1, 2]  # 出した順に積む
    assert out.discard_pile[-1].id == 2  # 最後がトップ
    assert out.current_player == "p2"


def test_multi_card_effect_hook_sees_played_cards():
    """効果フックは played_cards で出した群全体を参照できる（Draw2 累積等の土台）。"""
    seen = []

    def record(state, ctx):
        seen.append(tuple(c.id for c in (ctx.played_cards or ())))
        return state

    reg = build_registry(
        [PERMISSIVE, {CAN_STACK: lambda c, ctx: True}, {ON_AFTER_PLAY: record}]
    )
    apply_action(reg, _multi_state(), PlayAction("p1", (1, 2)))
    assert seen == [(1, 2)]


# --- #35 基盤拡張: UNO! / 指摘 は常時受理の割り込み --------------------------


def test_declare_uno_accepted_out_of_turn_without_advancing():
    """declare_uno は awaiting に無くても受理され、手番を消費しない（割り込み）。"""
    fired = []
    reg = build_registry([{ON_DECLARE_UNO: lambda s, ctx: fired.append(ctx.action.player) or s}])
    st = GameState.new_game(P, 1)  # awaiting = {p1: [play, draw]}、p2 は手番外
    out = apply_action(reg, st, DeclareUnoAction("p2"))  # 手番外の p2 でも受理
    assert fired == ["p2"]
    assert out.current_player == st.current_player  # 手番送りしない
    assert out.awaiting == st.awaiting  # awaiting も不変


def test_challenge_uno_accepted_out_of_turn():
    """challenge_uno（UNO言ってない!）も常時受理の割り込みで on_challenge_uno を回す。"""
    fired = []
    reg = build_registry([{ON_CHALLENGE_UNO: lambda s, ctx: fired.append(ctx.action.player) or s}])
    st = GameState.new_game(P, 1)
    apply_action(reg, st, ChallengeUnoAction("p2"))
    assert fired == ["p2"]
