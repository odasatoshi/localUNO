"""server/session.py の試験（issue #13 の完了条件を担保）。

- 切断→同トークン再接続で手札が復元される
- 3人目が拒否される
- 各クライアントに相手手札の中身が渡らない（視界フィルタ結合）
"""

from __future__ import annotations

import itertools
import random

import pytest

from lUNO.engine.actions import DrawAction, NewGameAction, PlayAction, ResetAction
from lUNO.engine.cards import CardInstance, CardType, Color
from lUNO.engine.state import GameEvent, GameState
from lUNO.rules import registry as build_registry
from lUNO.server.session import (
    PLAYER_IDS,
    Session,
    SessionError,
    SessionFull,
)


def make_session(seed: int = 1) -> Session:
    counter = itertools.count(1)
    return Session(seed=seed, token_factory=lambda: f"tok{next(counter)}")


# --- 接続・割当 -------------------------------------------------------------


def test_first_two_connections_get_p1_p2():
    s = make_session()
    a = s.connect()
    b = s.connect()
    assert a.player_id == "p1"
    assert b.player_id == "p2"
    assert a.token != b.token
    assert a.view.you == "p1"


def test_third_connection_rejected():
    s = make_session()
    s.connect()
    s.connect()
    with pytest.raises(SessionFull):
        s.connect()


def test_release_frees_slot_for_new_player():
    s = make_session()
    a = s.connect()
    s.connect()
    s.release(a.token)  # p1 離脱
    c = s.connect()  # 新規が p1 枠に入れる
    assert c.player_id == "p1"


# --- 再接続・多重接続 -------------------------------------------------------


def test_reconnect_same_token_restores_hand():
    s = make_session()
    a = s.connect()
    s.connect()
    # p1 が1枚引いて状態を変える
    s.apply(a.token, DrawAction("p1"))
    hand_after = s.view("p1").your_hand
    # 切断→同トークンで再接続
    s.disconnect(a.token)
    again = s.connect(token=a.token)
    assert again.reconnected is True
    assert again.player_id == "p1"
    assert again.view.your_hand == hand_after  # 手札が復元される


def test_multiple_connection_last_wins_returns_replaced():
    s = make_session()
    conn1 = object()
    a = s.connect(conn=conn1)
    conn2 = object()
    again = s.connect(token=a.token, conn=conn2)
    assert again.replaced is conn1  # 旧接続を返す（呼び出し側が閉じる）


def test_disconnect_only_clears_matching_conn():
    s = make_session()
    conn1 = object()
    a = s.connect(conn=conn1)
    conn2 = object()
    s.connect(token=a.token, conn=conn2)  # 後勝ちで conn2 が現行
    s.disconnect(a.token, conn=conn1)  # 古い conn1 での切断は無視される
    # 現行接続は残っているので再接続すると conn2 が replaced として返る
    result = s.connect(token=a.token, conn=object())
    assert result.replaced is conn2


def test_unknown_token_is_treated_as_new_player():
    s = make_session()
    r = s.connect(token="stale-unknown")
    assert r.player_id == "p1"
    assert r.reconnected is False
    assert r.token != "stale-unknown"  # 新トークンを発行


def test_reconnect_after_clean_disconnect_has_no_replaced():
    s = make_session()
    conn1 = object()
    a = s.connect(conn=conn1)
    s.connect()
    s.disconnect(a.token, conn=conn1)  # 綺麗に切断
    again = s.connect(token=a.token, conn=object())
    assert again.reconnected is True
    assert again.replaced is None  # 現行接続が無いので閉じる対象は無い


def test_stale_token_rejected_when_full():
    s = make_session()
    s.connect()
    s.connect()
    with pytest.raises(SessionFull):
        s.connect(token="stale")  # 満席では未知トークンの再入場も拒否


# --- Action 適用・権威 ------------------------------------------------------


def test_apply_rejects_unknown_token():
    s = make_session()
    with pytest.raises(SessionError):
        s.apply("nope", DrawAction("p1"))


def test_apply_rejects_player_mismatch():
    s = make_session()
    a = s.connect()  # p1
    s.connect()  # p2
    # p1 のトークンで p2 として行動しようとする → 拒否
    with pytest.raises(SessionError):
        s.apply(a.token, DrawAction("p2"))


def test_apply_accepts_json_action():
    s = make_session()
    a = s.connect()
    s.connect()
    before = len(s.view("p1").your_hand)
    s.apply(a.token, {"type": "draw", "player": "p1"})
    assert len(s.view("p1").your_hand) == before + 1


# --- 視界フィルタ結合（相手手札の中身が漏れない） ---------------------------


def test_views_do_not_leak_opponent_hand():
    s = make_session()
    a = s.connect()  # p1
    s.connect()  # p2
    views = s.views()
    assert set(views) == {"p1", "p2"}

    p1_dict = views["p1"].to_dict()
    opponent_ids = {c.id for c in s.state.hands["p2"]}
    leaked = _all_ids(p1_dict)
    assert opponent_ids.isdisjoint(leaked)  # 相手手札の個体は漏れない
    assert p1_dict["hand_counts"] == {"p1": 7, "p2": 7}  # 枚数のみ公開
    for forbidden in ("rng", "rng_state", "draw_pile", "hands"):
        assert forbidden not in p1_dict
    # 念のため p1 の view には自分の手札が見える
    assert {c.id for c in s.view("p1").your_hand} == {c.id for c in s.state.hands["p1"]}
    assert a.player_id == "p1"


# --- リセット（再戦） -------------------------------------------------------


def test_reset_keeps_tokens_and_redeals():
    s = make_session()
    a = s.connect()
    b = s.connect()
    s.apply(a.token, DrawAction("p1"))  # 盤面を動かす
    s.apply(a.token, ResetAction("p1"))
    # トークンは維持されたまま新しい配札
    assert s.player_of(a.token) == "p1"
    assert s.player_of(b.token) == "p2"
    assert len(s.view("p1").your_hand) == 7
    assert len(s.view("p2").your_hand) == 7
    assert s.state.current_player == "p1"


def test_reset_by_p2_also_works():
    s = make_session()
    a = s.connect()  # p1
    b = s.connect()  # p2
    s.apply(a.token, DrawAction("p1"))  # 盤面を動かす（p1 手番）
    s.apply(b.token, ResetAction("p2"))  # p2 からの再戦（reset は常時受理）
    assert len(s.view("p1").your_hand) == 7
    assert len(s.view("p2").your_hand) == 7


# --- new_game（ルール構成の切替＋再配札, #85） -----------------------------


def test_new_game_reconfigures_enabled_and_redeals():
    """new_game で有効ルール集合が差し替わり、盤面が新規に配られる。"""
    s = make_session()
    a = s.connect()
    s.connect()
    s.apply(a.token, DrawAction("p1"))  # 盤面を動かす
    s.apply(a.token, NewGameAction("p1", enabled_rule_ids=("reverse_off",)))
    assert s.enabled_ids == frozenset({"standard", "reverse_off"})  # standard は常時含む
    assert len(s.view("p1").your_hand) == 7  # 再配札
    assert len(s.view("p2").your_hand) == 7
    # rules_meta が新構成を反映（standard は required で常に有効、集合外は無効）
    meta = {m["id"]: m for m in s.rules_meta()}
    assert meta["reverse_off"]["enabled"] is True
    assert meta["standard"]["enabled"] is True
    assert meta["uno_call"]["enabled"] is False


def test_new_game_empty_is_standard_only():
    """空選択は標準のみ（ハウスルール全無効）で新規ゲームになる。"""
    s = make_session()
    a = s.connect()
    s.connect()
    s.apply(a.token, NewGameAction("p1", enabled_rule_ids=()))
    assert s.enabled_ids == frozenset({"standard"})  # required のみ
    meta = {m["id"]: m for m in s.rules_meta()}
    assert meta["standard"]["enabled"] is True  # required は常時
    assert all(not meta[k]["enabled"] for k in meta if k != "standard")


def test_new_game_rejects_unknown_rule_id():
    """未知のルールID を含む new_game は SessionError で弾く（構成ミスの黙認防止）。"""
    s = make_session()
    a = s.connect()
    s.connect()
    with pytest.raises(SessionError):
        s.apply(a.token, NewGameAction("p1", enabled_rule_ids=("does_not_exist",)))


def test_new_game_preserves_valid_order():
    """妥当な順序は保持され、ordered_ids・rules_meta に反映される（#92）。"""
    s = make_session()
    a = s.connect()
    s.connect()
    # multi_play を jump_in より前に（依存を満たす妥当順）
    s.apply(a.token, NewGameAction("p1", enabled_rule_ids=("multi_play", "jump_in")))
    assert s.ordered_ids == ("standard", "multi_play", "jump_in")
    meta_ids = [m["id"] for m in s.rules_meta()]
    assert meta_ids[:3] == ["standard", "multi_play", "jump_in"]


def test_new_game_rejects_order_violation():
    """前後依存を破る順序（jump_in を multi_play より前）は SessionError で弾く（#92）。"""
    s = make_session()
    a = s.connect()
    s.connect()
    with pytest.raises(SessionError, match="順序制約"):
        s.apply(a.token, NewGameAction("p1", enabled_rule_ids=("jump_in", "multi_play")))


def test_new_game_by_p2_also_works():
    """どちらのプレイヤーからでも new_game を開始できる。"""
    s = make_session()
    s.connect()  # p1
    b = s.connect()  # p2
    s.apply(b.token, NewGameAction("p2", enabled_rule_ids=("multi_play",)))
    assert s.enabled_ids == frozenset({"standard", "multi_play"})
    assert len(s.view("p1").your_hand) == 7


def _card(symbol, color, cid):
    from lUNO.engine.cards import CardInstance, CardType

    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def _fixed_setup(players, seed):
    """決定的な盤面: p1 は同数字の 7 を2枚（複数枚出し可否の観測用）。"""
    import random

    from lUNO.engine.cards import Color
    from lUNO.engine.state import GameState

    return GameState(
        hands={
            "p1": (_card("7", Color.RED, 1), _card("7", Color.BLUE, 2)),
            "p2": (_card("9", Color.GREEN, 3),),
        },
        draw_pile=(_card("0", Color.RED, 4),),
        discard_pile=(_card("3", Color.RED, 5),),  # 赤3: 赤7がリードとして合法
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": ("play", "draw")},
    )


def _fixed_session():
    counter = itertools.count(1)
    return Session(seed=1, token_factory=lambda: f"tok{next(counter)}", setup=_fixed_setup)


def test_new_game_rebuilds_registry_behaviorally_enable():
    """multi_play を含めて new_game すると、複数枚出しが実際に受理される（registry 再構築の担保）。

    rules_meta 反映だけでなく self._registry の組み直しを挙動で固定する
    （registry 再構築を削るとこのテストが落ちる）。
    """
    from lUNO.engine.actions import PlayAction

    s = _fixed_session()
    a = s.connect()
    s.connect()
    s.apply(a.token, NewGameAction("p1", enabled_rule_ids=("multi_play",)))
    s.apply(a.token, PlayAction("p1", card_ids=(1, 2)))  # 7 を2枚まとめ出し
    assert len(s.view("p1").your_hand) == 0  # 2枚出して上がり（受理された）


def test_new_game_rebuilds_registry_behaviorally_disable():
    """multi_play を外して new_game すると、複数枚出しが拒否される（registry 再構築の担保）。"""
    from lUNO.engine.actions import PlayAction
    from lUNO.engine.engine import IllegalAction

    s = _fixed_session()
    a = s.connect()
    s.connect()
    s.apply(a.token, NewGameAction("p1", enabled_rule_ids=()))  # 標準のみ
    with pytest.raises(IllegalAction):
        s.apply(a.token, PlayAction("p1", card_ids=(1, 2)))  # 複数枚は不可


# --- 配信対象（broadcast_targets） ------------------------------------------


def test_broadcast_targets_only_active_connections():
    s = make_session()
    conn1 = object()
    a = s.connect(conn=conn1)  # p1
    conn2 = object()
    s.connect(conn=conn2)  # p2
    targets = s.broadcast_targets()
    assert {c for c, _ in targets} == {conn1, conn2}

    # p1 を切断すると配信対象から外れる（views には残る）
    s.disconnect(a.token, conn=conn1)
    targets2 = s.broadcast_targets()
    assert {c for c, _ in targets2} == {conn2}
    assert set(s.views()) == {"p1", "p2"}  # views は切断中も含む


def test_reset_is_deterministic():
    a = make_session(7)
    a.connect()
    a.connect()
    b = make_session(7)
    b.connect()
    b.connect()
    ta = list(a._slots)  # 内部だが seed 一致確認のため
    a.apply(ta[0], ResetAction("p1"))
    tb = list(b._slots)
    b.apply(tb[0], ResetAction("p1"))
    assert a.state == b.state


# helpers -------------------------------------------------------------------


def _all_ids(obj: object) -> set[int]:
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


def test_player_ids_are_two():
    assert PLAYER_IDS == ("p1", "p2")


# --- 連勝カットイン（#108） -------------------------------------------------


def _card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def _near_win_state(players, seed) -> GameState:
    """p1 が RED7 を1枚持ち、場は RED5。p1 が (1,) を出すと即上がりになる盤面。

    連勝の積み上げを決定的に再現するため、reset のたびに同じ「あと1手で p1 勝ち」を作る。
    """
    return GameState(
        hands={"p1": (_card("7", Color.RED, 1),), "p2": (_card("9", Color.GREEN, 2),)},
        draw_pile=(_card("0", Color.RED, 6), _card("1", Color.BLUE, 7)),
        discard_pile=(_card("5", Color.RED, 90),),
        current_player="p1",
        rng_state=random.Random(seed).getstate(),
        awaiting={"p1": ("play", "draw")},
    )


def _streak_session() -> Session:
    """standard のみ・毎ゲーム『あと1手で p1 勝ち』の決定的セッション。"""
    counter = itertools.count(1)
    return Session(
        seed=1,
        token_factory=lambda: f"tok{next(counter)}",
        registry=build_registry(frozenset({"standard"})),
        setup=_near_win_state,
    )


def _alternating_win_session() -> tuple[Session, dict]:
    """勝者を切り替えられる決定的セッション。返す dict の ``winner`` を書き換えると、
    次のセットアップ（reset/new_game）で『その人があと1手で勝つ』盤面を作る。"""
    board = {"winner": "p1"}
    counter = itertools.count(1)

    def setup(players, seed) -> GameState:
        w = board["winner"]
        loser = "p2" if w == "p1" else "p1"
        # 勝者に RED7（場の RED5 に出せる・id=1）、敗者に GREEN9（id=2）を持たせる。
        hands = {w: (_card("7", Color.RED, 1),), loser: (_card("9", Color.GREEN, 2),)}
        return GameState(
            hands=hands,
            draw_pile=(_card("0", Color.RED, 6),),
            discard_pile=(_card("5", Color.RED, 90),),
            current_player=w,
            rng_state=random.Random(seed).getstate(),
            awaiting={w: ("play", "draw")},
        )

    s = Session(
        seed=1,
        token_factory=lambda: f"tok{next(counter)}",
        registry=build_registry(frozenset({"standard"})),
        setup=setup,
    )
    return s, board


def test_first_win_has_no_streak_event():
    """1勝目は連勝ではないのでカットイン（win_streak）を出さない。"""
    s = _streak_session()
    a = s.connect()
    s.connect()
    st = s.apply(a.token, PlayAction("p1", (1,)))
    assert st.winner == "p1"
    assert st.last_event is None


def test_reset_continues_streak_and_second_win_emits_event():
    """reset（同設定で再戦）は連勝を継続。2連勝目で本人向けの win_streak が載る。"""
    s = _streak_session()
    a = s.connect()
    s.connect()
    s.apply(a.token, PlayAction("p1", (1,)))  # 1勝目
    s.apply(a.token, ResetAction("p1"))  # 再戦（連勝継続）
    st = s.apply(a.token, PlayAction("p1", (1,)))  # 2勝目
    assert st.winner == "p1"
    assert st.last_event == GameEvent("win_streak", by="p1", amount=2)


def test_streak_accumulates_across_multiple_resets():
    """3連勝で amount=3 まで積み上がる。"""
    s = _streak_session()
    a = s.connect()
    s.connect()
    s.apply(a.token, PlayAction("p1", (1,)))
    s.apply(a.token, ResetAction("p1"))
    s.apply(a.token, PlayAction("p1", (1,)))
    s.apply(a.token, ResetAction("p1"))
    st = s.apply(a.token, PlayAction("p1", (1,)))
    assert st.last_event == GameEvent("win_streak", by="p1", amount=3)


def test_new_game_resets_streak():
    """new_game（ルール構成変更）は仕切り直しで連勝をリセットする。

    観測ベース: 2連勝を作った後 new_game すると、直後の勝利は「1連勝目」に戻り
    win_streak イベントが出ない（リセットされなければ3連勝＝amount=3 が出るはず）。
    """
    s = _streak_session()
    a = s.connect()
    s.connect()
    s.apply(a.token, PlayAction("p1", (1,)))  # 1勝目
    s.apply(a.token, ResetAction("p1"))
    s.apply(a.token, PlayAction("p1", (1,)))  # 2勝目（win_streak が出ている）
    s.apply(a.token, NewGameAction("p1", ("standard",)))  # 仕切り直し＝連勝リセット
    st = s.apply(a.token, PlayAction("p1", (1,)))  # new_game 後の1勝目
    assert st.winner == "p1"
    assert st.last_event is None  # リセット済みなので連勝カットインは出ない


def test_non_winning_action_emits_no_streak_event():
    """勝者が立たないアクション（自主ドロー等）では連勝イベントを出さない（据え置き）。"""
    s = _streak_session()
    a = s.connect()
    s.connect()
    st = s.apply(a.token, DrawAction("p1"))  # 勝者は立たない
    assert st.winner is None
    assert st.last_event is None


def test_win_by_different_player_restarts_streak():
    """勝者が変わったら連勝は1から数え直す（前の勝者の連勝を引き継がない）。

    観測ベース: p1 が1勝 → 勝者が p2 に変わって p2 が勝つと、p2 は1連勝目なので
    win_streak は出ない（引き継いでいれば誤って amount=2 の連勝が出てしまう）。
    """
    s, board = _alternating_win_session()
    a = s.connect()
    b = s.connect()
    s.apply(a.token, PlayAction("p1", (1,)))  # p1 が1勝目（イベントなし）
    board["winner"] = "p2"  # 次の盤面は p2 が上がる
    s.apply(a.token, ResetAction("p1"))  # 再戦
    st = s.apply(b.token, PlayAction("p2", (1,)))  # 今度は p2 が勝つ
    assert st.winner == "p2"
    assert st.last_event is None  # 勝者交代＝p2 の1連勝目なので連勝カットインは出ない
