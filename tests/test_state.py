"""engine/state.py の試験（spec.md §3.1/§3.6/§5 の完了条件を担保）。

- PlayerView に山札順序・相手手札中身・RNG が含まれない（漏れ防止）
- 固定シードで再現的
- ネットワーク/描画に非依存（engine 純粋性）
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from lUNO.engine import state as state_mod
from lUNO.engine.actions import DrawAction, PlayAction
from lUNO.engine.cards import CardInstance, CardType, Color
from lUNO.engine.state import GameState, player_view

PLAYERS = ("p1", "p2")


def make_state(seed: int = 42) -> GameState:
    return GameState.new_game(PLAYERS, seed=seed)


# --- 初期状態・決定性 --------------------------------------------------------


def test_new_game_deal_counts_conserved():
    st = make_state()
    assert len(st.hands["p1"]) == 7
    assert len(st.hands["p2"]) == 7
    assert len(st.draw_pile) == 108 - 14
    assert st.discard_pile == ()
    # 個体の総数・一意性が保たれる
    all_ids = (
        [c.id for c in st.hands["p1"]]
        + [c.id for c in st.hands["p2"]]
        + [c.id for c in st.draw_pile]
    )
    assert len(all_ids) == 108
    assert len(set(all_ids)) == 108


def test_new_game_initial_awaiting_and_turn():
    st = make_state()
    assert st.current_player == "p1"
    assert st.awaiting == {"p1": (PlayAction.type, DrawAction.type)}
    assert st.forced_color is None
    assert st.direction == 1
    assert st.pending_draw == 0


def test_new_game_deterministic_with_seed():
    """同一シードなら配札・盤面・RNG 状態まで再現的。== が RNG まで含めて健全。"""
    a = make_state(7)
    b = make_state(7)
    assert a == b  # 手札・山札・捨て山・手番・rng_state まで完全一致
    assert a.rng_state == b.rng_state


def test_eq_detects_rng_divergence():
    """RNG が発散した2状態は == で等しくならない（決定性検証の穴を塞ぐ）。"""
    a = make_state(7)
    _, a_advanced = a.with_rng(lambda rng: rng.random())
    assert a_advanced != a  # rng_state が進んだので不等
    b = make_state(7)
    assert a == b  # 消費していない側は一致


def test_with_rng_is_pure_and_deterministic():
    """with_rng は入力 state の rng_state を進めず、同シードで同結果を返す。"""
    a = make_state(7)
    r1, a2 = a.with_rng(lambda rng: [rng.random() for _ in range(3)])
    # 入力 state は不変
    assert a.rng_state == make_state(7).rng_state
    assert a2.rng_state != a.rng_state
    # 別の同シード state から同じ操作 → 同じ結果（再現性）
    b = make_state(7)
    r2, _ = b.with_rng(lambda rng: [rng.random() for _ in range(3)])
    assert r1 == r2


def test_new_game_differs_with_seed():
    a = make_state(1)
    b = make_state(2)
    assert a != b


def test_new_game_requires_two_players():
    with pytest.raises(ValueError):
        GameState.new_game(("only",), seed=1)


# --- 視界フィルタ: 漏れ防止（完了条件の中核） --------------------------------


def test_player_view_hides_draw_pile_and_opponent_hand():
    st = make_state()
    view = player_view(st, "p1")

    # 本人の手札は中身が見える
    assert view.your_hand == st.hands["p1"]
    # 枚数だけは全員公開
    assert view.hand_counts == {"p1": 7, "p2": 7}
    assert view.draw_count == len(st.draw_pile)

    # dict 化して「秘匿対象の個体 ID / RNG / 生データ」が漏れていないことを検査
    d = view.to_dict()
    leaked = _all_ids(d)
    opponent_ids = {c.id for c in st.hands["p2"]}
    draw_ids = {c.id for c in st.draw_pile}
    assert opponent_ids.isdisjoint(leaked)
    assert draw_ids.isdisjoint(leaked)

    # 秘匿フィールドのキーが存在しない（ホワイトリスト方式）
    for forbidden in ("rng", "draw_pile", "hands", "seed"):
        assert forbidden not in d


def test_player_view_has_no_rng_attribute():
    view = player_view(make_state(), "p1")
    assert not hasattr(view, "rng")
    assert "rng" not in view.to_dict()


def test_player_view_public_fields():
    # 捨て山トップ・強制色などが公開されることを確認
    st = make_state()
    top = CardInstance(CardType(symbol="5", color=Color.GREEN, label="5"), id=999)
    st = st.replace(
        discard_pile=(top,),
        forced_color=Color.RED,
        pending_draw=2,
        direction=-1,
        current_player="p2",
    )
    view = player_view(st, "p1")
    assert view.top_of_pile == top
    assert view.forced_color == Color.RED
    assert view.pending_draw == 2
    assert view.direction == -1
    assert view.current_player == "p2"

    d = view.to_dict()
    assert d["top_of_pile"]["id"] == 999
    assert d["forced_color"] == "red"
    assert d["top_of_pile"]["color"] == "green"


def test_player_view_top_of_pile_none_when_empty():
    view = player_view(make_state(), "p1")
    assert view.top_of_pile is None
    assert view.to_dict()["top_of_pile"] is None


def test_player_view_rejects_unknown_player():
    with pytest.raises(ValueError):
        player_view(make_state(), "nobody")


def test_player_view_is_pure_no_mutation():
    """視界フィルタは GameState を変更しない純関数。"""
    st = make_state()
    before = (st.hands, st.draw_pile, st.awaiting)
    player_view(st, "p1")
    assert (st.hands, st.draw_pile, st.awaiting) == before


# --- 永続フィールドの不変更新 ------------------------------------------------


def test_with_setters_return_new_state_without_mutation():
    st = make_state()
    st2 = st.with_pending_draw(4)
    assert st2.pending_draw == 4
    assert st.pending_draw == 0  # 元は不変
    assert st2 is not st

    assert st.with_forced_color(Color.BLUE).forced_color == Color.BLUE
    assert st.with_current_player("p2").current_player == "p2"
    assert st.with_direction(-1).direction == -1


def test_with_awaiting_normalizes_to_tuples():
    st = make_state()
    st2 = st.with_awaiting({"p2": ["play"]})
    assert st2.awaiting == {"p2": ("play",)}
    assert st.awaiting == {"p1": (PlayAction.type, DrawAction.type)}  # 元は不変


def test_frozen_state_is_immutable():
    st = make_state()
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.pending_draw = 9  # type: ignore[misc]


def test_hands_and_awaiting_reject_in_place_mutation():
    """hands / awaiting は読み取り専用ビュー。in-place 変更を弾く（§3.2 の所有権）。"""
    st = make_state()
    with pytest.raises(TypeError):
        st.hands["p1"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        st.awaiting["p2"] = ("play",)  # type: ignore[index]


def test_player_view_dicts_reject_in_place_mutation():
    view = player_view(make_state(), "p1")
    with pytest.raises(TypeError):
        view.hand_counts["p2"] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        view.awaiting["p1"] = ()  # type: ignore[index]


def test_other_player():
    st = make_state()
    assert st.other_player("p1") == "p2"
    assert st.other_player("p2") == "p1"


def test_winner_default_none_and_setter_and_view():
    """winner は既定 None（進行中）。with_winner で不変更新し、PlayerView に公開される。"""
    st = make_state()
    assert st.winner is None
    won = st.with_winner("p1")
    assert won.winner == "p1"
    assert st.winner is None  # 元は不変
    view = player_view(won, "p2")
    assert view.winner == "p1"
    assert view.to_dict()["winner"] == "p1"


def test_uno_declared_default_setter_and_view():
    """uno_declared は既定空。with_uno_declared で不変更新し、PlayerView に公開される。"""
    st = make_state()
    assert st.uno_declared == frozenset()
    d = st.with_uno_declared({"p1"})
    assert d.uno_declared == frozenset({"p1"})
    assert st.uno_declared == frozenset()  # 元は不変
    view = player_view(d, "p2")  # 相手からも宣言状態は見える（指摘の判定に必要）
    assert view.uno_declared == frozenset({"p1"})
    assert view.to_dict()["uno_declared"] == ["p1"]


# --- engine 純粋性: ネットワーク/描画非依存 ----------------------------------


def test_state_module_has_no_network_or_render_deps():
    """state.py が network/描画/画像生成モジュールを import しないこと。"""
    src = inspect.getsource(state_mod)
    forbidden = ("fastapi", "uvicorn", "starlette", "PIL", "socket", "requests", "httpx")
    for name in forbidden:
        assert name not in src, f"engine 純粋性違反: {name} を参照している"


# helpers -------------------------------------------------------------------


def _all_ids(obj: object) -> set[int]:
    """ネストした dict/list から "id" 値を再帰収集する。"""
    found: set[int] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "id" and isinstance(v, int):
                found.add(v)
            else:
                found |= _all_ids(v)
    elif isinstance(obj, list):
        for item in obj:
            found |= _all_ids(item)
    return found
