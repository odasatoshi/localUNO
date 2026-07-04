"""ローカルルール: リバース無効化（docs/house-rules.md §1）。

リバースを効果なしの通常カードにする。標準ルール（standard.py）は二人対戦で
リバースを SKIP と同様に扱い「出した本人の手番を継続」させる（``awaiting`` に
本人の受理集合を立てる）が、本ルールはそれを打ち消し、**通常どおり相手へ手番を
渡す**。色/記号一致で出せる点は standard の ``can_play`` のまま（リバースは記号
``reverse`` を保持するので、色違いのリバース同士も出せる）。

有効化リストで standard の**後ろ**に置く（後勝ち上書き、§3.4）。engine は改修せず
rules/ 内で完結する（原則3）。
"""

from __future__ import annotations

from ..engine.cards import REVERSE
from ..engine.hooks import ON_AFTER_PLAY, Ctx, Rule
from ..engine.state import GameState


def reverse_has_no_effect(state: GameState, ctx: Ctx) -> GameState:
    """リバースの「本人手番継続（スキップ化）」を打ち消し、通常の手番送りへ戻す。

    standard の ``apply_effect`` が立てた ``awaiting`` をクリアすると、engine の既定の
    手番送り（相手へ、受理集合 ``[play, draw]``）が働く。終局（winner 確定）時は
    ``check_winner`` が既に受理集合を閉じているので触らない。
    """
    if ctx.card is not None and ctx.card.symbol == REVERSE and state.winner is None:
        return state.with_awaiting({})
    return state


RULES: Rule = {
    ON_AFTER_PLAY: reverse_has_no_effect,
}
