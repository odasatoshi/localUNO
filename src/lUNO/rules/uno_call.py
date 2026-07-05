"""ローカルルール: UNO 宣言＋指摘（docs/house-rules.md §6）。

手札が1枚になる手番では「UNO!」宣言が必須。宣言忘れは相手が「UNO言ってない!」で
指摘でき、成功なら宣言し忘れた側が2枚引く。誤爆（相手が1枚でない/既に宣言済み＝相手の
UNO! 後）は押した本人が2枚引く。手札1枚でない「UNO!」宣言（誤宣言）も本人が2枚引く。
LAN 前提で遅延・同時押しの競合は考慮しない。

engine（#35）が `DeclareUnoAction`/`ChallengeUnoAction`（常時受理の割り込み）と
`ON_DECLARE_UNO`/`ON_CHALLENGE_UNO` フック、`uno_declared` フィールドを既に用意して
いるので、本ルールはそこへ判定・ペナルティ・フラグ整理を足すだけ（rules/ 完結）。
ペナルティのドローは engine の `draw_cards`（山切れ再シャッフル込み）を再利用する。
"""

from __future__ import annotations

from ..engine.engine import ON_CHALLENGE_UNO, ON_DECLARE_UNO, draw_cards
from ..engine.hooks import ON_AFTER_PLAY, ON_DRAW, Ctx, Rule
from ..engine.state import GameEvent, GameState

_PENALTY = 2


def _is_over(state: GameState) -> bool:
    """終局か（上がり or 山切れ引き分け）。終局後の UNO 宣言/指摘は無効。"""
    return state.winner is not None or state.is_draw


def _refresh_declared(state: GameState) -> GameState:
    """手札が1枚でないプレイヤーの宣言済みフラグを解除する（枚数変化で宣言は無効）。"""
    valid = frozenset(p for p in state.uno_declared if len(state.hands.get(p, ())) == 1)
    if valid != state.uno_declared:
        return state.with_uno_declared(valid)
    return state


def declare_uno(state: GameState, ctx: Ctx) -> GameState:
    """「UNO!」: 宣言者の手札が**1枚のときだけ**有効。1枚なら宣言成立（宣言済みへ）、
    既に宣言済みなら no-op（集合の再追加）。

    手札が**1枚でない**のに宣言する**誤宣言**は、押した本人が **2枚**引く（指摘誤爆と
    同じペナルティ）。終局後（上がり／山切れ引き分け）は無効（challenge_uno と対称。
    手札0枚もここに含まれる）。
    """
    if _is_over(state):
        return state  # 終局後の宣言は無効
    player = ctx.action.player
    if len(state.hands.get(player, ())) == 1:
        return state.with_uno_declared(state.uno_declared | {player}).with_last_event(
            GameEvent("uno", by=player)
        )
    # 手札が1枚でない誤宣言は本人が2枚ドロー（指摘誤爆と対称）。ドロー後の
    # フラグ整理は challenge_uno と対称に置く（本経路では実質 no-op だが防御的）。
    state = draw_cards(state, player, _PENALTY)
    state = _refresh_declared(state)
    return state.with_last_event(
        GameEvent("uno_misfire", by=player, target=player, amount=_PENALTY)
    )


def challenge_uno(state: GameState, ctx: Ctx) -> GameState:
    """「UNO言ってない!」: 相手が1枚かつ未宣言なら相手が2枚（成功）、そうでなければ
    押した本人が2枚（誤爆）。終局後（上がり／山切れ引き分け）の指摘は無効。"""
    if _is_over(state):
        return state  # 終局後の指摘は無効
    challenger = ctx.action.player
    target = state.other_player(challenger)
    caught = len(state.hands.get(target, ())) == 1 and target not in state.uno_declared
    penalized = target if caught else challenger
    state = draw_cards(state, penalized, _PENALTY)
    state = _refresh_declared(state)
    kind = "challenge_success" if caught else "challenge_misfire"
    return state.with_last_event(
        GameEvent(kind, by=challenger, target=penalized, amount=_PENALTY)
    )


def refresh_after_change(state: GameState, ctx: Ctx) -> GameState:
    """手札枚数が変わる play/draw の後に宣言済みフラグを整理する。"""
    return _refresh_declared(state)


RULES: Rule = {
    ON_DECLARE_UNO: declare_uno,
    ON_CHALLENGE_UNO: challenge_uno,
    ON_AFTER_PLAY: refresh_after_change,
    ON_DRAW: refresh_after_change,
}
