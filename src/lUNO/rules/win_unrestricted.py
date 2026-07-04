"""ローカルルール: 上がり制限撤廃（docs/house-rules.md §5）。

standard.py は ``no_win_on_wild`` 制限で「手札最後の1枚がワイルド系なら出せない」
（ワイルドで上がれない）としているが、本ハウスルールはこれを撤廃し、**Wild /
Wild Draw4 を含む任意のカードを最後の1枚として出して上がれる**ようにする。

``can_play`` 値リデューサに「ワイルドは常に許可」を後勝ちで足すだけ（§3.4）。有効化
リストで standard（＝``no_win_on_wild``）の**後ろ**に置く。engine 無改修・rules/ 完結。
"""

from __future__ import annotations

from ..engine.hooks import CAN_PLAY, Ctx, Rule


def allow_win_on_wild(current: bool, ctx: Ctx) -> bool:
    """ワイルド系は最後の1枚でも出せる（standard の no_win_on_wild を後勝ちで撤廃）。

    ワイルドは元来 ``standard_can_play`` が常に許可しており、``current`` が False に
    なる唯一の要因が ``no_win_on_wild``（最後の1枚制限）。ここで True を返して復活させる。
    """
    if ctx.card is not None and ctx.card.is_wild:
        return True
    return current


RULES: Rule = {
    CAN_PLAY: allow_win_on_wild,
}
