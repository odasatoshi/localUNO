"""rules/jump_in.py の試験（issue #27）。

- 完全一致で手番外の割り込みが可能
- 不一致（色/記号のみ一致含む）では割り込めない
- 割り込んだら手番はそのプレイヤーの次へ進む
- engine 差分ゼロ（rules 内で完結）
"""

from __future__ import annotations

import pytest

from lUNO.engine.actions import PlayAction
from lUNO.engine.cards import CardInstance, CardType, Color
from lUNO.engine.engine import IllegalAction, apply_action
from lUNO.engine.hooks import build_registry
from lUNO.engine.state import GameState
from lUNO.rules import jump_in, registry, standard

P = ("p1", "p2")


def card(symbol, color, cid):
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def reg():
    # standard の後ろに jump_in（制限は許可の後ろ、§3.4）
    return build_registry([standard.RULES, jump_in.RULES])


def state_with_jump_slot(*, p1, p2, top, current="p2"):
    """手番外割り込み枠が張られた状態（p2 手番中、p1 に割り込み枠）。"""
    off_turn = "p1" if current == "p2" else "p2"
    return GameState(
        hands={"p1": p1, "p2": p2},
        draw_pile=(card("0", Color.YELLOW, 90), card("1", Color.YELLOW, 91)),
        discard_pile=(top,),
        current_player=current,
        rng_state=GameState.new_game(P, seed=0).rng_state,
        awaiting={current: ("play", "draw"), off_turn: ("play",)},
    )


# --- 完全一致で割り込み可 ---------------------------------------------------


def test_exact_match_jump_in_allowed_and_turn_advances():
    r = reg()
    top = card("5", Color.RED, 10)
    st = state_with_jump_slot(
        p1=(card("5", Color.RED, 1), card("9", Color.BLUE, 2)),  # 赤5 で割り込める
        p2=(card("3", Color.GREEN, 3),),
        top=top,
        current="p2",
    )
    out = apply_action(r, st, PlayAction("p1", card_ids=(1,)))  # p1 が手番外で割り込み
    assert out.discard_pile[-1].id == 1  # 割り込んだ札が場に乗る
    assert len(out.hands["p1"]) == 1  # p1 の手札が減る
    # 割り込んだら手番は p1 の次 = p2 へ
    assert out.current_player == "p2"
    assert out.awaiting.get("p2") == ("play", "draw")
    # 新しい手番外枠は p1 に張り直される
    assert out.awaiting.get("p1") == ("play",)


# --- 不一致では割り込めない -------------------------------------------------


def test_color_only_match_cannot_jump_in():
    """色だけ一致（記号違い）では手番外で割り込めない（完全一致でないため）。"""
    r = reg()
    top = card("5", Color.RED, 10)
    st = state_with_jump_slot(
        p1=(card("9", Color.RED, 1),),  # 赤9: 色は一致だが記号が違う
        p2=(card("3", Color.GREEN, 3),),
        top=top,
    )
    with pytest.raises(IllegalAction):
        apply_action(r, st, PlayAction("p1", card_ids=(1,)))


def test_special_card_cannot_jump_in_off_turn():
    """特殊札（同一 CardType でも）は手番外割り込み対象外＝弾く（土台スコープ）。"""
    r = reg()
    top = card("skip", Color.RED, 10)
    st = state_with_jump_slot(
        p1=(card("skip", Color.RED, 1), card("9", Color.BLUE, 2)),  # 赤skip 完全一致だが特殊札
        p2=(card("3", Color.GREEN, 3),),
        top=top,
    )
    with pytest.raises(IllegalAction):
        apply_action(r, st, PlayAction("p1", card_ids=(1,)))


def test_symbol_only_match_cannot_jump_in():
    """記号だけ一致（色違い）でも割り込めない。"""
    r = reg()
    top = card("5", Color.RED, 10)
    st = state_with_jump_slot(
        p1=(card("5", Color.BLUE, 1),),  # 青5: 記号は一致だが色が違う
        p2=(card("3", Color.GREEN, 3),),
        top=top,
    )
    with pytest.raises(IllegalAction):
        apply_action(r, st, PlayAction("p1", card_ids=(1,)))


# --- 手番中のプレイには干渉しない -------------------------------------------


def test_on_turn_play_still_uses_standard_matching():
    """手番プレイは standard どおり色/記号一致で出せる（ジャンプイン制限は手番外のみ）。"""
    r = reg()
    top = card("5", Color.RED, 10)
    st = state_with_jump_slot(
        p1=(card("9", Color.BLUE, 1),),
        # 赤3: 色一致で手番中に出せる。もう1枚持たせて上がりを避ける
        p2=(card("3", Color.RED, 3), card("8", Color.GREEN, 4)),
        top=top,
        current="p2",
    )
    out = apply_action(r, st, PlayAction("p2", card_ids=(3,)))  # p2 は手番
    assert out.discard_pile[-1].id == 3
    assert out.current_player == "p1"  # 手番が p1 へ


# --- 手番送りで割り込み枠が張られる（統合） --------------------------------


def test_jump_slot_is_established_after_a_normal_turn():
    """新規ゲームから1手進めると、手番外プレイヤーに割り込み枠が張られる。"""
    r = reg()
    st = standard.setup_game(P, seed=3)
    # p1 が手札から場に出せる札を1枚出す（無ければ引く）
    top = st.discard_pile[-1]
    # 数字カードに限定（スキップ/リバース等だと 2人では手番が自分に残るため）
    playable = next(
        (
            c
            for c in st.hands["p1"]
            if c.symbol.isdigit() and (c.color == top.color or c.symbol == top.symbol)
        ),
        None,
    )
    if playable is None:
        pytest.skip("この seed では初手に出せる数字札が無い")
    out = apply_action(r, st, PlayAction("p1", card_ids=(playable.id,)))
    assert out.current_player == "p2"
    assert out.awaiting.get("p2") == ("play", "draw")
    assert out.awaiting.get("p1") == ("play",)  # 手番外の p1 に割り込み枠


# --- engine 差分ゼロ（rules 内で完結） --------------------------------------


def test_jump_in_win_by_exact_match():
    """割り込みで最後の1枚（完全一致・数字）を出すと上がりになる（終局を閉じる）。"""
    r = reg()
    top = card("5", Color.RED, 10)
    st = state_with_jump_slot(
        p1=(card("5", Color.RED, 1),),  # これが最後の1枚
        p2=(card("3", Color.GREEN, 3),),
        top=top,
        current="p2",
    )
    out = apply_action(r, st, PlayAction("p1", card_ids=(1,)))
    assert out.winner == "p1"
    assert out.awaiting == {}  # 終局: 受理集合は空
    assert out.pending_draw == 0


def test_jump_in_works_in_full_enabled_stack():
    """full ENABLED_RULES（他ローカルルール共存）でもジャンプインが機能する。"""
    r = registry()
    top = card("5", Color.RED, 10)
    st = state_with_jump_slot(
        p1=(card("5", Color.RED, 1), card("9", Color.BLUE, 2)),
        p2=(card("3", Color.GREEN, 3),),
        top=top,
        current="p2",
    )
    out = apply_action(r, st, PlayAction("p1", card_ids=(1,)))
    assert out.discard_pile[-1].id == 1
    assert out.current_player == "p2"  # 割り込み後は p1 の次 = p2
    # 不一致は full stack でも弾かれる
    st2 = state_with_jump_slot(
        p1=(card("9", Color.RED, 1),),  # 色のみ一致
        p2=(card("3", Color.GREEN, 3),),
        top=top,
    )
    with pytest.raises(IllegalAction):
        apply_action(r, st2, PlayAction("p1", card_ids=(1,)))


def test_jump_in_is_pure_rules_no_engine_hooks_beyond_public():
    """jump_in は公開フック名（can_play/on_after_play/on_turn_end）だけで表現される。"""
    from lUNO.engine.hooks import CAN_PLAY, ON_AFTER_PLAY, ON_TURN_END

    assert set(jump_in.RULES.keys()) == {CAN_PLAY, ON_AFTER_PLAY, ON_TURN_END}
