"""ローカルルール: 複数枚出し（docs/house-rules.md §2）。

同じ数字、または同じ記号（マーク）のカードを複数枚まとめて出せる。engine（#35）は
``card_ids`` 複数・``played_cards``・``can_stack`` ゲートの受け皿を既に持つので、本ルールは
そこへ「合法性（can_stack）」と「効果（Draw2 のみ枚数分累積）」を足すだけ（rules/ 完結）。

合法性（``can_stack`` 値リデューサ, シード False）:
- 先頭カードの場への合法性は engine が ``can_play`` で判定済み。``can_stack`` は
  **群の同質性**のみを見る。
- 判定は「群の全カードが先頭と**同じ記号（symbol）**」。数字カードの記号は数字そのもの
  （"7" 等）なので、これは「同じ数字 or 同じマーク」を一意に表し、かつ「数字と記号の
  混在不可」も自動的に満たす（"7" と "skip" は記号不一致）。ワイルド同士（記号 "wild"）／
  Wild Draw4 同士（"draw4"）のみ許可され、両者混在は記号不一致で弾かれる（§2）。

効果（``on_after_play`` state トランスフォーマ）:
- **Draw2 のみ枚数分累積**（§2）。standard の ``apply_effect`` は最後のカード基準で Draw2
  効果を1回（+2）適用済みなので、本ルールは残り (n-1) 枚分の +2 を追加する。
- スキップ等は複数出しても効果1回（standard の適用のまま）。数字は効果なし。ワイルドの
  色指定は最後の1回（standard が最後のワイルドで色選択待ちを立てる）。
"""

from __future__ import annotations

from ..engine.cards import DRAW2
from ..engine.hooks import CAN_STACK, ON_AFTER_PLAY, Ctx, Rule
from ..engine.state import GameState


def same_symbol_group(current: bool, ctx: Ctx) -> bool:
    """複数枚出しの群が全て同じ記号なら許可する（同数字 or 同マーク、混在不可）。"""
    if current:
        return True
    played = ctx.played_cards
    if played is None or len(played) <= 1:
        return current  # 単数出しは can_stack を経由しない（engine が len>1 のみ問う）
    lead_symbol = played[0].card_type.symbol
    return all(c.card_type.symbol == lead_symbol for c in played)


def accumulate_extra_draw2(state: GameState, ctx: Ctx) -> GameState:
    """複数枚出しで Draw2 が2枚以上なら、standard が適用した1枚分に残りを足す（§2）。"""
    played = ctx.played_cards
    if played is None or len(played) <= 1 or state.winner is not None:
        return state
    n_draw2 = sum(1 for c in played if c.card_type.symbol == DRAW2)
    if n_draw2 > 1:
        # standard の apply_effect が最後の Draw2 で +2 済み。残り (n_draw2-1) 枚分を追加。
        return state.with_pending_draw(state.pending_draw + 2 * (n_draw2 - 1))
    return state


RULES: Rule = {
    CAN_STACK: same_symbol_group,
    ON_AFTER_PLAY: accumulate_extra_draw2,
}
