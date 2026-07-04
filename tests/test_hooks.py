"""engine/hooks.py の試験（spec.md §3.2/§3.3/§3.4 の完了条件を担保）。

- 値リデューサ/トランスフォーマ双方の合成
- can_play の OR/AND（許可追加＋後段制限）
- 記述順で結果が変わる
"""

from __future__ import annotations

import pytest

from lUNO.engine.actions import DrawAction
from lUNO.engine.cards import DRAW2, DRAW4, CardInstance, CardType, Color
from lUNO.engine.hooks import (
    CAN_PLAY,
    ON_BEFORE_PLAY,
    SCORE,
    Ctx,
    HookRegistry,
    build_registry,
    reduce_value,
    transform_state,
)
from lUNO.engine.state import GameState

PLAYERS = ("p1", "p2")


def make_ctx(
    card: CardInstance | None = None,
    top: CardInstance | None = None,
    action=None,
) -> Ctx:
    state = GameState.new_game(PLAYERS, seed=1)
    return Ctx(
        state=state,
        current_player="p1",
        action=action,
        card=card,
        hand=state.hands["p1"],
        top_of_pile=top,
    )


def card(symbol: str, color: Color | None, cid: int = 0) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


# --- 値リデューサの合成（後勝ち） --------------------------------------------


def test_reduce_value_folds_in_order_last_wins():
    handlers = [
        lambda v, ctx: v + 1,
        lambda v, ctx: v * 10,
        lambda v, ctx: v - 3,
    ]
    # (((0 + 1) * 10) - 3) = 7
    assert reduce_value(handlers, 0, make_ctx()) == 7


def test_reduce_value_empty_returns_seed():
    assert reduce_value([], 42, make_ctx()) == 42


# --- state トランスフォーマの合成（チェーン） -------------------------------


def test_transform_state_chains():
    def set_pending_5(state, ctx):
        return state.with_pending_draw(5)

    def add_2(state, ctx):
        return state.with_pending_draw(state.pending_draw + 2)

    st = GameState.new_game(PLAYERS, seed=1)
    ctx = make_ctx()
    out = transform_state([set_pending_5, add_2], st, ctx)
    assert out.pending_draw == 7
    assert st.pending_draw == 0  # 元 state は不変


def test_draw2_stack_accumulates_via_state_field():
    """累積は「現在の state 値を読んで加算し書き戻す」で表現される（rule-authoring 例）。"""

    def draw2_stack(state, ctx):
        if ctx.card.symbol != DRAW2:
            return state
        return state.with_pending_draw(state.pending_draw + 2)

    st = GameState.new_game(PLAYERS, seed=1)
    ctx = make_ctx(card=card(DRAW2, Color.RED))
    # 2枚重ねる（シードは常に現在の GameState 値なので積み上がる）
    st = transform_state([draw2_stack], st, ctx)
    st = transform_state([draw2_stack], st, ctx)
    assert st.pending_draw == 4


# --- can_play の OR/AND 合成意味論（§3.4） -----------------------------------


def standard_can_play(current, ctx):
    if current:
        return True
    c, top = ctx.card, ctx.top_of_pile
    if c.is_wild:
        return True
    return c.color == top.color or c.symbol == top.symbol


def jump_in(current, ctx):
    if current:
        return True
    return ctx.card.card_type == ctx.top_of_pile.card_type  # 完全一致


def draw4_only_when_no_alternative(current, ctx):
    """制限ルール: ドロー4は他に出せる札が無いときのみ。"""
    if not current:
        return False
    if ctx.card.symbol != DRAW4:
        return current
    # 手札に top と色/記号一致の非ワイルドがあれば「他に出せる」= 却下
    top = ctx.top_of_pile
    has_alt = any(
        (not x.is_wild) and (x.color == top.color or x.symbol == top.symbol)
        for x in ctx.hand
    )
    return not has_alt


def test_can_play_seed_false_denies_by_default():
    reg = build_registry([])  # ルール無し
    ctx = make_ctx(card=card("7", Color.RED), top=card("3", Color.BLUE))
    assert reg.can_play(ctx) is False


def test_can_play_permit_rule_adds_true_or():
    reg = build_registry([{CAN_PLAY: standard_can_play}])
    # 色一致 → 許可
    ctx = make_ctx(card=card("7", Color.RED, 1), top=card("3", Color.RED, 2))
    assert reg.can_play(ctx) is True
    # 不一致・非ワイルド → 却下
    ctx2 = make_ctx(card=card("7", Color.RED, 1), top=card("3", Color.BLUE, 2))
    assert reg.can_play(ctx2) is False


def test_can_play_or_addition_across_permit_rules():
    """許可を足す2ルールの OR 合成: 前段が False でも後段の許可ルールが True を足す（§3.4）。"""
    # 標準では出せない: card=青7, top=赤3 → False
    ctx = make_ctx(card=card("7", Color.BLUE, 1), top=card("3", Color.RED, 2))

    def allow_all_sevens(current, c):
        if current:  # 既に許可済みならそのまま（OR 的追加）
            return True
        return c.card.symbol == "7"  # 7 を無条件許可するローカルルール

    only_standard = build_registry([{CAN_PLAY: standard_can_play}])
    added = build_registry([{CAN_PLAY: standard_can_play}, {CAN_PLAY: allow_all_sevens}])

    assert only_standard.can_play(ctx) is False  # 標準単体は却下
    assert added.can_play(ctx) is True  # 後段の許可ルールが OR で True を足す


def test_jump_in_exact_match_permits():
    """jump_in は場と完全一致（CardType 同一）で許可を足す。"""
    reg = build_registry([{CAN_PLAY: jump_in}])
    top = card("7", Color.RED, 2)
    assert reg.can_play(make_ctx(card=card("7", Color.RED, 99), top=top)) is True
    assert reg.can_play(make_ctx(card=card("7", Color.BLUE, 99), top=top)) is False


def _ctx(hand, top, played):
    state = GameState.new_game(PLAYERS, seed=1)
    return Ctx(state=state, current_player="p1", card=played, hand=hand, top_of_pile=top)


def test_can_play_restrict_rule_overrides_after_permit():
    """制限ルールを許可ルールの後ろに置くと、許可を却下できる（§3.4）。"""
    reg = build_registry(
        [
            {CAN_PLAY: standard_can_play},  # 許可（wild は常に True）
            {CAN_PLAY: draw4_only_when_no_alternative},  # 制限（後段）
        ]
    )
    top = card("5", Color.RED, 2)
    draw4 = card("draw4", None, 11)
    # 手札に「出せる代替札」(赤2) がある → 標準は draw4 を許可するが制限が却下
    assert reg.can_play(_ctx((card("2", Color.RED, 10), draw4), top, draw4)) is False
    # 代替が無ければ許可のまま
    assert reg.can_play(_ctx((draw4,), top, draw4)) is True


# --- 記述順で結果が変わる ----------------------------------------------------


def test_description_order_changes_result():
    """制限を許可の前に置くと（前段で False→後段で True 上書き）結果が変わる。"""
    top = card("5", Color.RED, 2)
    draw4 = card("draw4", None, 11)
    ctx = _ctx((card("2", Color.RED, 10), draw4), top, draw4)

    restrict_after = build_registry(
        [{CAN_PLAY: standard_can_play}, {CAN_PLAY: draw4_only_when_no_alternative}]
    )
    restrict_before = build_registry(
        [{CAN_PLAY: draw4_only_when_no_alternative}, {CAN_PLAY: standard_can_play}]
    )
    # 後段が後勝ち: 制限を後ろ→却下(False)、制限を前→許可(True) と結果が反転する
    assert restrict_after.can_play(ctx) is False
    assert restrict_before.can_play(ctx) is True


# --- score / seed / registry ------------------------------------------------


def test_score_default_seed_zero_and_compose():
    reg = build_registry(
        [
            {SCORE: lambda v, ctx: v + 20},
            {SCORE: lambda v, ctx: v + 5},
        ]
    )
    assert reg.score(make_ctx()) == 25


def test_registry_preserves_order_across_and_within_rules():
    log: list[str] = []

    def mark(name):
        def h(s, c):
            log.append(name)
            return s

        return h

    rule_a = {ON_BEFORE_PLAY: [mark("a1"), mark("a2")]}
    rule_b = {ON_BEFORE_PLAY: mark("b")}
    reg = build_registry([rule_a, rule_b])
    st = GameState.new_game(PLAYERS, seed=1)
    reg.transform(ON_BEFORE_PLAY, st, make_ctx())
    assert log == ["a1", "a2", "b"]


def test_reduce_uses_value_seeds_when_seed_omitted():
    reg = build_registry([{CAN_PLAY: lambda v, ctx: True}])
    # seed 省略時は VALUE_SEEDS[CAN_PLAY]=False から畳み込む
    assert reg.reduce(CAN_PLAY, make_ctx()) is True


def test_ctx_surface_is_accessible():
    c = card("7", Color.GREEN, 3)
    t = card("1", Color.GREEN, 4)
    seen = {}

    def probe(v, ctx):
        seen["action"] = ctx.action
        seen["card"] = ctx.card
        seen["hand"] = ctx.hand
        seen["top"] = ctx.top_of_pile
        seen["cur"] = ctx.current_player
        seen["state"] = ctx.state
        return v

    act = DrawAction(player="p1")
    ctx = make_ctx(card=c, top=t, action=act)
    reduce_value([probe], 0, ctx)
    assert seen["action"] is act
    assert seen["card"] is c and seen["top"] is t
    assert seen["cur"] == "p1"
    assert isinstance(seen["state"], GameState)


def test_handlers_returns_empty_for_unknown_hook():
    reg = HookRegistry()
    assert reg.handlers("nonexistent") == ()


# --- seed 宣言・センチネル ---------------------------------------------------


def test_reduce_raises_for_unregistered_value_reducer_without_seed():
    reg = build_registry([{"custom_value": lambda v, ctx: v}])
    with pytest.raises(ValueError):
        reg.reduce("custom_value", make_ctx())  # seed 未登録＆省略 → 明示エラー


def test_register_seed_and_build_registry_seeds():
    ctx = make_ctx()
    reg = build_registry([{"threshold": lambda v, ctx: v + 1}], seeds={"threshold": 100})
    assert reg.reduce("threshold", ctx) == 101
    reg2 = build_registry([{"threshold": lambda v, ctx: v + 1}])
    reg2.register_seed("threshold", 5)
    assert reg2.reduce("threshold", ctx) == 6


def test_explicit_seed_allows_none():
    """seed= を明示すれば None も正当なシードとして使える（センチネルと区別）。"""
    reg = build_registry([{"maybe": lambda v, ctx: v if v is not None else "was_none"}])
    assert reg.reduce("maybe", make_ctx(), seed=None) == "was_none"


# --- Ctx.from_state ファクトリ -----------------------------------------------


def test_ctx_from_state_fills_from_state():
    st = GameState.new_game(PLAYERS, seed=1)
    st = st.replace(discard_pile=(card("5", Color.GREEN, 500),))
    ctx = Ctx.from_state(st)
    assert ctx.current_player == st.current_player == "p1"
    assert ctx.top_of_pile == st.top_of_pile()
    assert ctx.hand == st.hands["p1"]


def test_ctx_from_state_owner_for_off_turn():
    """手番外評価では owner に相手を指定でき、current_player は手番のまま。"""
    st = GameState.new_game(PLAYERS, seed=1)
    ctx = Ctx.from_state(st, owner="p2")
    assert ctx.current_player == "p1"  # 手番は権威（state 由来）
    assert ctx.hand == st.hands["p2"]  # 評価対象は相手手札


def test_ctx_from_state_rejects_unknown_owner():
    """未知 owner は沈黙 None ではなく ValueError（player_view と一貫）。"""
    st = GameState.new_game(PLAYERS, seed=1)
    with pytest.raises(ValueError):
        Ctx.from_state(st, owner="typo")


# --- transform_state の戻り値ガード ------------------------------------------


def test_transform_state_rejects_non_state_return():
    """return state を忘れたハンドラ（None 返し）を早期に検出する。"""
    st = GameState.new_game(PLAYERS, seed=1)

    def forgets_return(state, ctx):
        state.with_pending_draw(1)  # 戻り値を返し忘れ → None

    with pytest.raises(TypeError):
        transform_state([forgets_return], st, make_ctx())
