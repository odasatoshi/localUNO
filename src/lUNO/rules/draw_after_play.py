"""ローカルルール: ドロー後プレイ／自主ドロー（docs/house-rules.md §7）。

手番中に山から引けるのは1枚のみ（自主ドロー可＝出せる札があっても引ける。これは
engine の受理集合が常に ``[play, draw]`` を許すため既に成立）。本ルールが足すのは
**引いた後の選択**: 引いた札が場に合法ならそのターン中に出せる（引いた札を先頭に
複数枚出しも可）。出さずにパスもできる。引いた札が出せなければ実質パス。

engine（#40 基盤）が ``PassAction``・``GameState.drawn_card_id``・``Ctx.drawn_cards`` を
用意済み。本ルールは:
- ``on_draw`` ``offer_play_after_draw``: **自主ドロー（1枚）**の後、``drawn_card_id`` を
  立てて受理集合を ``[play, pass]`` にし、手番を保持する（＝engine の既定手番送りを止める）。
  強制ドロー（Draw2/4 で複数枚引く）では発火しない（引いた枚数で判定）。
- ``can_play`` ``only_drawn_card_leads``: ドロー後フェーズ（``drawn_card_id`` あり）は
  引いた札を先頭にした出しだけを許す（制限＝後勝ちで却下）。
- ``on_after_play`` ``clear_drawn_marker``: 出したらマーカーを解除する。

パスは engine の ``PassAction`` がマーカー解除＋手番送りを行う（rules/ 側の実装不要）。
"""

from __future__ import annotations

from ..engine.hooks import CAN_PLAY, ON_AFTER_PLAY, ON_DRAW, Ctx, Rule
from ..engine.state import GameState

_AFTER_DRAW = ("play", "pass")


def offer_play_after_draw(state: GameState, ctx: Ctx) -> GameState:
    """自主ドロー（1枚）後、引いた札を出す or パスの選択を与える。

    強制ドロー（Draw2/4 で複数引く）や終局時は発火せず、engine の既定手番送りに任せる。
    """
    drawn = ctx.drawn_cards
    if state.winner is not None or drawn is None or len(drawn) != 1:
        return state
    player = ctx.action.player
    return state.with_drawn_card_id(drawn[0].id).with_awaiting({player: _AFTER_DRAW})


def only_drawn_card_leads(current: bool, ctx: Ctx) -> bool:
    """ドロー後フェーズ（drawn_card_id あり）は引いた札を先頭にした出しだけ許す。"""
    if not current:
        return False
    marker = ctx.state.drawn_card_id
    if marker is not None and ctx.card is not None and ctx.card.id != marker:
        return False
    return True  # ここに来る時点で current は True（先頭の if で False は弾いている）


def clear_drawn_marker(state: GameState, ctx: Ctx) -> GameState:
    """出したらドロー後マーカーを解除する（フェーズ終了）。"""
    if state.drawn_card_id is not None:
        return state.with_drawn_card_id(None)
    return state


RULES: Rule = {
    ON_DRAW: offer_play_after_draw,
    CAN_PLAY: only_drawn_card_leads,
    ON_AFTER_PLAY: clear_drawn_marker,
}
