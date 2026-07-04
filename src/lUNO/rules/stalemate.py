"""ローカルルール: 山切れ行き詰まりの引き分け決着（docs/house-rules.md §8）。

山札が空で捨て山も再シャッフル不能（トップ1枚以下＝戻せる札が無い）＝これ以上
ドローで補充できず、かつ両プレイヤーとも場のトップ（強制色があればその色）に出せる
札を1枚も持たない場合、ゲームは進行不能になる（誰も play できず、draw も0枚で手番が
往復するだけ）。この行き詰まりを検出し、勝敗をつけず**引き分け**（``is_draw``）で
終局させる。無限にドローを押し合うライブロックを断つ。

engine は終局判定（``_advance_if_idle``）で ``winner`` と同様に ``is_draw`` を終局として
扱い、手番送りを止める。本ルールは ``on_turn_end`` で行き詰まりを検出して ``is_draw`` を
立て ``awaiting`` を空にする。``ENABLED_RULES`` の**末尾**（jump_in より後）に置くことで、
jump_in が立てた割り込み枠 ``awaiting`` を上書きし、確実に終局させる。

判定は base の出せる条件（色 / 記号一致・強制色・ワイルドは常に可）で行う。上がり制限は
撤廃済み（#39）なのでワイルドは常に出せ、ワイルドを持つ側は行き詰まりに該当しない。

**同期義務**: ``_can_play_any`` は ``standard.standard_can_play``（＋上がり制限撤廃後の
base）を手書きで再現している。トランスフォーマ型フックは HookRegistry を受け取れず
can_play リデューサを直接呼べないための割り切り。将来 base の合法判定が変わる／別の
「出せる」経路を足す局所ルールが入る場合は、見逃し（進行可能なのに引き分け＝誤検出）を
避けるため本関数も同期すること。なお ``draw_after_play``（#40）の「引いた札を出す」経路は
山切れ時は0枚ドローで発火しないため見逃しにならない。
"""

from __future__ import annotations

from ..engine.cards import CardInstance, Color
from ..engine.hooks import ON_TURN_END, Ctx, Rule
from ..engine.state import GameState


def _deck_unrefillable(state: GameState) -> bool:
    """山札が空で、捨て山がトップ1枚以下＝再シャッフルで戻せる札が無い。"""
    return not state.draw_pile and len(state.discard_pile) <= 1


def _can_play_any(
    hand: tuple[CardInstance, ...], top: CardInstance | None, forced: Color | None
) -> bool:
    """手札に、場のトップ（強制色があればその色）へ出せる札が1枚でもあるか（base 条件）。"""
    for card in hand:
        if card.is_wild:
            return True
        if top is None:
            return True
        if forced is not None:
            if card.color == forced:
                return True
        elif card.color == top.color or card.symbol == top.symbol:
            return True
    return False


def resolve_stalemate(state: GameState, ctx: Ctx) -> GameState:
    """山切れ行き詰まりなら引き分けで終局させる（house-rules §8）。"""
    if state.winner is not None or state.is_draw:
        return state  # 既に終局
    if not _deck_unrefillable(state):
        return state  # まだ引ける（＝行き詰まりでない）
    top = state.top_of_pile()
    forced = state.forced_color
    if any(_can_play_any(hand, top, forced) for hand in state.hands.values()):
        return state  # どちらかが出せる＝進行可能
    # 誰も出せず・補充もできない → 引き分けで終局（手番送り・受理を止める）
    return state.with_is_draw(True).with_awaiting({}).with_pending_draw(0)


RULES: Rule = {ON_TURN_END: resolve_stalemate}
