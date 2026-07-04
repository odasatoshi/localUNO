"""ローカルルール: Draw2 スタック（docs/house-rules.md §3）。

standard は Draw2 を出された受け手を「引く」だけに制限するが、本ハウスルールでは
受け手は **Draw2 を1枚出して返せる**（枚数・色不問）。返された側もまた返せ、連鎖する。
返さない/返せない側が累積した ``pending_draw`` を全部引く（engine の draw 機構が担う）。
Draw4（Wild Draw4）はこのスタックに無関係（Draw2 と混在・相互スタックしない）。

実装（engine 無改修・rules/ 完結）:
- **can_play 制限** ``only_draw2_during_stack``: ``pending_draw>0``（Draw2 スタック応答中）
  は Draw2 以外を出せない（返しは Draw2 に限る）。制限ルールなので後勝ちで False を返す。
- **ON_AFTER_PLAY** ``allow_draw2_return``: standard が Draw2 効果で受け手を「引くだけ」に
  した受理集合へ ``play`` を足し、Draw2 での返しを可能にする。返さず draw すれば engine が
  ``pending_draw`` を全部引いて 0 に戻し、手番が進む。

Draw4 の pending 中は standard が受理集合を「引くだけ」にしており play を足さないため、
本ルールは Draw4 スタックを誘発しない（§3 の「Draw4 は無関係」と整合）。
"""

from __future__ import annotations

from ..engine.cards import DRAW2
from ..engine.hooks import CAN_PLAY, ON_AFTER_PLAY, Ctx, Rule
from ..engine.state import GameState

# 受け手の受理集合: 引く or Draw2 で返す
_DRAW_OR_RETURN = ("draw", "play")


def only_draw2_during_stack(current: bool, ctx: Ctx) -> bool:
    """Draw2 累積中は Draw2 以外を出せない（返しは Draw2 に限る、色不問）。"""
    if not current:
        return False
    if ctx.state.pending_draw > 0 and ctx.card is not None and ctx.card.symbol != DRAW2:
        return False
    return current


def allow_draw2_return(state: GameState, ctx: Ctx) -> GameState:
    """Draw2 を出したら、受け手（standard が既に手番を移した相手）に Draw2 返しを許可する。

    standard の Draw2 効果は ``pending_draw`` を加算し受理集合を「引くだけ」にする。ここで
    ``play`` を足すことで受け手が Draw2 を1枚出して返せるようになる（連鎖はこの繰り返し）。
    終局時（winner 確定）は触らない。
    """
    if (
        ctx.card is not None
        and ctx.card.symbol == DRAW2
        and state.winner is None
        and state.pending_draw > 0
    ):
        receiver = state.current_player  # standard が既に相手へ手番を移している
        return state.with_awaiting({receiver: _DRAW_OR_RETURN})
    return state


RULES: Rule = {
    CAN_PLAY: only_draw2_during_stack,
    ON_AFTER_PLAY: allow_draw2_return,
}
