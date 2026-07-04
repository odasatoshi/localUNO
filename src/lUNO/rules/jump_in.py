"""ローカルルール: ジャンプイン（#27）。

手番でなくても、場（捨て山トップ）と**完全一致**（色・数字/記号すべて一致 = 同一
CardType）のカードを持っていれば割り込んで出せる。割り込んで出したら手番はその
プレイヤーの次へ進む。

engine 非改修（rules/ 完結）で、engine の次の性質だけを使う:
- `awaiting` はプレイヤー別マップ。手番外プレイヤーの受理集合に `play` を足せば、engine は
  そのプレイヤーの `play` を受理する（spec §3.6）。
- can_play の ctx は `action`（出す人）と `current_player`（現手番）の両方を持つ。両者が
  異なれば「手番外プレイ」＝ジャンプインと判定できる。
- 効果適用後に `awaiting` が空なら engine が既定の手番送りを行う。`on_turn_end` はその直前に
  走るので、ここで手番送りと同時に相手の割り込み枠を張る。

3つのフックで表現する:
1. can_play 制限 `only_exact_off_turn`: 手番外プレイは**完全一致に限る**。standard は色/記号
   一致で許可してしまうため、手番外では同一 CardType 以外を却下する（記述順は standard の後ろ）。
2. on_after_play `claim_turn_on_jump_in`: 手番外プレイなら出した人を現手番に据える。これで
   後続の手番送りが「出した人の次」へ正しく進む（応答待ちを立てる特殊札プレイ時は触らない）。
3. on_turn_end `enable_jump_in`: 既定の手番送り（相手へ）を行いつつ、手番外プレイヤーに
   `("play",)` の割り込み枠を張る（一致限定は 1. が担保）。

限定: 特殊札（スキップ/ドロー2/ワイルド）を手番外で割り込んだ場合の手番制御は standard の
効果が `current_player`（手番者）基準で動くため厳密でない。土台では数字カード等の通常
ジャンプインを対象とする（完全一致の割り込み可否・手番遷移が完了条件）。
"""

from __future__ import annotations

from ..engine.hooks import CAN_PLAY, ON_AFTER_PLAY, ON_TURN_END, Ctx, Rule
from ..engine.state import GameState

_TURN = ("play", "draw")
_JUMP = ("play",)  # 手番外は割り込み play のみ（一致限定は can_play が担保）


def only_exact_off_turn(current: bool, ctx: Ctx) -> bool:
    """手番外プレイ（出す人 ≠ 現手番）は場と完全一致（同一 CardType）に限る（制限）。

    standard は色/記号一致で許可するが、ジャンプインは完全一致のみ。手番中のプレイには
    干渉しない（standard の判定をそのまま通す）。
    """
    if not current:
        return False
    action = ctx.action
    if action is not None and action.player != ctx.current_player:
        top = ctx.top_of_pile
        if top is None or ctx.card is None or ctx.card.card_type != top.card_type:
            return False
    return current


def claim_turn_on_jump_in(state: GameState, ctx: Ctx) -> GameState:
    """手番外プレイなら出した人を現手番に据える（手番送りを正しくするため）。

    応答待ちを立てる特殊札プレイ時（`awaiting` 非空）は触らない。通常の割り込み
    （数字カード等）でのみ、出した人を手番者にしてから on_turn_end に渡す。
    """
    action = ctx.action
    if action is not None and action.player != state.current_player and not state.awaiting:
        return state.with_current_player(action.player)
    return state


def enable_jump_in(state: GameState, ctx: Ctx) -> GameState:
    """手番送り（相手へ）と同時に、手番外プレイヤーへ一致プレイの割り込み枠を張る。

    engine の `_advance_if_idle` 内（既定手番送りの直前）に走る。`awaiting` を明示的に
    立てるので engine の既定手番送りは走らず、ここで手番送り＋割り込み枠を確定する。
    """
    actor = state.current_player  # claim_turn 済みなら割り込んだ本人、通常は手番者
    other = state.other_player(actor)
    return state.with_current_player(other).with_awaiting({other: _TURN, actor: _JUMP})


RULES: Rule = {
    CAN_PLAY: only_exact_off_turn,
    ON_AFTER_PLAY: claim_turn_on_jump_in,
    ON_TURN_END: enable_jump_in,
}
