"""標準 UNO のルールセット（リファレンス実装）。

spec.md §1.1/§3 と rule-authoring.md に沿い、標準 UNO を「1つのルールセット」として
フックで表現する。判定は ``can_play`` 値リデューサ、効果は state トランスフォーマで書く。
**engine は改修せず rules/ 内で完結**する（原則3）。

二人対戦特有の扱い（spec §3.2）:
- スキップ／リバースは実質同じ（相手を飛ばす＝自分の手番が続く）。
- ドロー2／ワイルドドロー4 は、相手に累積ドロー(``pending_draw``)を課し「引く」だけを
  受理集合に残して手番を渡す。相手がドローすると（engine の draw 機構が pending を
  消費し）手番が自分へ戻る＝実質スキップ。

上がり制限（base 標準）: ワイルド／ワイルドドロー4 を最後の1枚にして上がれない
（``no_win_on_wild`` 制限ルール）。この制限の撤廃はローカルルール（#39）で行う。
"""

from __future__ import annotations

from ..engine.cards import DRAW2, DRAW4, REVERSE, SKIP, CardInstance
from ..engine.hooks import (
    CAN_PLAY,
    ON_AFTER_PLAY,
    ON_CHOOSE_COLOR,
    SCORE,
    Ctx,
    Rule,
)
from ..engine.state import GameState

# 受理集合の定型（Action.type 名）
_TURN = ("play", "draw")
_DRAW_ONLY = ("draw",)
_CHOOSE = ("choose_color",)


# --- can_play（値リデューサ, §3.4） -----------------------------------------


def standard_can_play(current: bool, ctx: Ctx) -> bool:
    """色/数字/記号の一致、ワイルドは常に可。強制色があればそれに従う（許可を足す）。"""
    if current:  # 既に許可済みならそのまま（OR 的追加）
        return True
    card = ctx.card
    if card.is_wild:
        return True
    top = ctx.top_of_pile
    if top is None:
        return True  # 場が空なら何でも出せる
    forced = ctx.state.forced_color
    if forced is not None:
        return card.color == forced  # 直前のワイルドが指定した色に従う
    return card.color == top.color or card.symbol == top.symbol


def no_win_on_wild(current: bool, ctx: Ctx) -> bool:
    """制限: 手札最後の1枚がワイルド系なら出せない（ワイルドで上がれない）。

    許可ルールの後ろに置く（§3.4）。撤廃はローカルルール #39。
    """
    if not current:
        return False
    if ctx.card.is_wild and ctx.hand is not None and len(ctx.hand) == 1:
        return False
    return current


# --- 効果（state トランスフォーマ, §3.2） -----------------------------------


def apply_effect(state: GameState, ctx: Ctx) -> GameState:
    """出したカードの記号に応じた効果を適用する。"""
    card = ctx.card
    actor = ctx.current_player
    symbol = card.symbol

    if card.is_wild:
        # ワイルド / ワイルドドロー4: 本人の色選択待ちで停止（§3.6）
        if symbol == DRAW4:
            state = state.with_pending_draw(state.pending_draw + 4)
        return state.with_awaiting({actor: _CHOOSE})

    # 色付きカード: 直前の強制色を解除（top が色を持つため）
    state = state.with_forced_color(None)

    if symbol == DRAW2:
        opponent = state.other_player(actor)
        state = state.with_pending_draw(state.pending_draw + 2)
        # 相手は「引く」だけ。引くと手番が自分へ戻る（2人での skip 相当）
        return state.with_current_player(opponent).with_awaiting({opponent: _DRAW_ONLY})

    if symbol in (SKIP, REVERSE):
        # 2人では相手を飛ばす＝自分の手番が続く
        return state.with_awaiting({actor: _TURN})

    # 数字カード: 追加効果なし（engine が既定で相手へ手番送り）
    return state


def apply_choose_color(state: GameState, ctx: Ctx) -> GameState:
    """ワイルドの色選択を確定する（継続フック, §3.6）。forced_color は rules が書く（§3.2）。"""
    actor = ctx.current_player
    state = state.with_forced_color(ctx.action.color)
    if state.pending_draw > 0:
        # ワイルドドロー4: 色確定後、相手に強制ドローを課す
        opponent = state.other_player(actor)
        return state.with_current_player(opponent).with_awaiting({opponent: _DRAW_ONLY})
    # 通常ワイルド: engine が既定で相手へ手番送り
    return state


def check_winner(state: GameState, ctx: Ctx) -> GameState:
    """カードを出した本人の手札が尽きたら上がり（winner を立てて終局状態を閉じる）。

    アクションカード（スキップ/ドロー2等）で上がった場合、直前の :func:`apply_effect`
    が受理集合 ``awaiting`` を立てているため、ここで空にしないと終局後もクライアントの
    行動が受理されてしまう（engine の受理判定は winner を見ず awaiting のみ見る）。
    そこで受理集合・累積ドローをクリアし、手番を勝者に戻して終局を確定させる。
    """
    actor = ctx.current_player
    if actor in state.hands and len(state.hands[actor]) == 0:
        return (
            state.with_winner(actor)
            .with_awaiting({})
            .with_pending_draw(0)
            .with_current_player(actor)
        )
    return state


# --- 得点（値リデューサ） ----------------------------------------------------


def card_points(card: CardInstance) -> int:
    """標準 UNO の得点: 数字=数値、記号=20、ワイルド系=50。"""
    if card.is_wild:
        return 50
    if card.symbol in (SKIP, REVERSE, DRAW2):
        return 20
    return int(card.symbol)  # 数字カード


def standard_score(current: int, ctx: Ctx) -> int:
    """上がり時の得点: 勝者は相手の残り手札の合計点を得る。"""
    state = ctx.state
    if state.winner is None:
        return current
    loser = state.other_player(state.winner)
    return current + sum(card_points(c) for c in state.hands[loser])


# --- ルール束（記述順で登録される） -----------------------------------------

RULES: Rule = {
    CAN_PLAY: [standard_can_play, no_win_on_wild],  # 許可 → 制限（§3.4）
    ON_AFTER_PLAY: [apply_effect, check_winner],  # 効果 → 上がり判定
    ON_CHOOSE_COLOR: apply_choose_color,
    SCORE: standard_score,
}


# --- ゲーム開始のセットアップ（場札めくり） ---------------------------------


def setup_game(players, seed: int, hand_size: int = 7) -> GameState:
    """標準 UNO の初期状態。配札後に場札を1枚めくって捨て山トップにする。

    最初の場札がワイルド系だと強制色が未確定になるため、非ワイルドが出るまでめくり、
    めくったワイルドは山の底へ戻す（決定的）。engine を使わず GameState だけで完結。

    土台の割り切り: 開始札がアクションカード（スキップ/リバース/ドロー2）でも、その
    効果（先手スキップ・方向反転・先手ドロー2）は**適用しない**（先手はそのまま
    play/draw できる）。開始札効果を厳密に適用したい場合はローカルルール/セッション層で
    初手に apply_effect を噛ませる拡張として足す。
    """
    state = GameState.new_game(players, seed, hand_size)
    draw = list(state.draw_pile)  # index0=底, index-1=上
    skipped_wilds: list[CardInstance] = []
    top: CardInstance | None = None
    while draw:
        candidate = draw.pop()  # 上から
        if candidate.is_wild:
            skipped_wilds.append(candidate)
            continue
        top = candidate
        break
    if top is None:
        raise ValueError("非ワイルドカードが山に無い")
    new_draw = tuple(skipped_wilds + draw)  # ワイルドは底へ戻す
    return state.replace(draw_pile=new_draw, discard_pile=(top,))
