"""ローカルルール: UNO 宣言＋指摘（docs/house-rules.md §6）。

手札が1枚になる手番では「UNO!」宣言が必須。宣言忘れは相手が「UNO言ってない!」で
指摘でき、成功なら宣言し忘れた側が2枚引く。誤爆（相手が1枚でない/既に宣言済み＝相手の
UNO! 後）は押した本人が2枚引く。LAN 前提で遅延・同時押しの競合は考慮しない。

engine（#35）が `DeclareUnoAction`/`ChallengeUnoAction`（常時受理の割り込み）と
`ON_DECLARE_UNO`/`ON_CHALLENGE_UNO` フック、`uno_declared` フィールドを既に用意して
いるので、本ルールはそこへ判定・ペナルティ・フラグ整理を足すだけ（rules/ 完結）。
ペナルティのドローは engine の `draw_cards`（山切れ再シャッフル込み）を再利用する。
"""

from __future__ import annotations

from ..engine.engine import ON_CHALLENGE_UNO, ON_DECLARE_UNO, draw_cards
from ..engine.hooks import ON_AFTER_PLAY, ON_DRAW, Ctx, Rule
from ..engine.state import GameState

_PENALTY = 2


def _refresh_declared(state: GameState) -> GameState:
    """手札が1枚でないプレイヤーの宣言済みフラグを解除する（枚数変化で宣言は無効）。"""
    valid = frozenset(p for p in state.uno_declared if len(state.hands.get(p, ())) == 1)
    if valid != state.uno_declared:
        return state.replace(uno_declared=valid)
    return state


def declare_uno(state: GameState, ctx: Ctx) -> GameState:
    """「UNO!」: 宣言者の手札が**1枚のときだけ**有効（それ以外は空押し・ペナルティなし）。"""
    player = ctx.action.player
    if len(state.hands.get(player, ())) == 1:
        return state.replace(uno_declared=state.uno_declared | {player})
    return state


def challenge_uno(state: GameState, ctx: Ctx) -> GameState:
    """「UNO言ってない!」: 相手が1枚かつ未宣言なら相手が2枚（成功）、そうでなければ
    押した本人が2枚（誤爆）。終局後の指摘は無効。"""
    if state.winner is not None:
        return state  # 終局後の指摘は無効
    challenger = ctx.action.player
    target = state.other_player(challenger)
    caught = len(state.hands.get(target, ())) == 1 and target not in state.uno_declared
    penalized = target if caught else challenger
    state = draw_cards(state, penalized, _PENALTY)
    return _refresh_declared(state)


def refresh_after_change(state: GameState, ctx: Ctx) -> GameState:
    """手札枚数が変わる play/draw の後に宣言済みフラグを整理する。"""
    return _refresh_declared(state)


RULES: Rule = {
    ON_DECLARE_UNO: declare_uno,
    ON_CHALLENGE_UNO: challenge_uno,
    ON_AFTER_PLAY: refresh_after_change,
    ON_DRAW: refresh_after_change,
}
