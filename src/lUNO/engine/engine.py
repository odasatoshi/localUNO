"""Action を受けフックを回して新しい GameState を返す純粋ロジック（spec.md §2/§3.3/§3.6）。

エンジンは**ルール非依存のオーケストレータ**。次の3層で構成する:

1. **受理判定** — ``awaiting`` に照合し、今そのプレイヤーが取れない Action は拒否する
   （``reset`` は §8 のメタ操作として常時受理）。
2. **フック評価順**（§3.3）— play は「can_play(値リデューサ) → 効果適用(state
   トランスフォーマ on_before_play/on_after_play) → 手番送り」。効果の実体は rules が
   フックで与え、エンジンは普遍的な機構（カード移動・ドロー・山切れ再シャッフル・
   既定の手番送り）だけを担う。永続フィールドの書き換えは state トランスフォーマ経由
   （§3.2）。
3. **応答待ちライフサイクル**（§3.6）— 受理した Action の処理開始時に ``awaiting`` を
   クリアし、効果フックが新たな ``awaiting`` を立てればそこで停止（例: ワイルドの色
   選択待ち）。効果後に ``awaiting`` が空なら、エンジンが既定の手番送り（二人対戦は
   相手へ、受理集合を ``[play, draw]`` に）を行う。

非決定要素（山切れ再シャッフル）は GameState の RNG（:meth:`GameState.with_rng`）を
通し、同じ seed＋同じ Action 列 → 同じ GameState を保証する（§3.5）。ネットワーク・
描画には依存しない（原則2）。
"""

from __future__ import annotations

from collections.abc import Iterable

from .actions import (
    Action,
    ChooseColorAction,
    DeclareUnoAction,
    DrawAction,
    PlayAction,
    ResetAction,
)
from .cards import CardInstance
from .hooks import (
    ON_AFTER_PLAY,
    ON_BEFORE_PLAY,
    ON_CHOOSE_COLOR,
    ON_DRAW,
    ON_TURN_END,
    Ctx,
    HookRegistry,
)
from .state import GameState

STANDARD_TURN_ACTIONS = (PlayAction.type, DrawAction.type)
ON_DECLARE_UNO = "on_declare_uno"


class EngineError(Exception):
    """エンジンが Action を処理できないときの基底例外。"""


class IllegalAction(EngineError):
    """受理集合に無い／不整合な Action（awaiting 不一致・手札に無いカード・出せない札）。"""


# --- 公開エントリ ------------------------------------------------------------


def apply_action(reg: HookRegistry, state: GameState, action: Action) -> GameState:
    """1つの Action を適用して新しい GameState を返す（純関数）。"""
    _check_accepted(state, action)
    handler = _DISPATCH.get(action.type)
    if handler is None:  # 保険: 既知種別は _check_accepted で先に弾かれ通常到達しない
        raise IllegalAction(f"未対応の Action 種別: {action.type!r}")
    return handler(reg, state, action)


def apply_actions(reg: HookRegistry, state: GameState, actions: Iterable[Action]) -> GameState:
    """Action 列を順に適用する（決定性検証などの便宜）。"""
    for action in actions:
        state = apply_action(reg, state, action)
    return state


# --- 受理判定（§3.6） -------------------------------------------------------


def _check_accepted(state: GameState, action: Action) -> None:
    if action.type == ResetAction.type:
        return  # reset は常時受理（§8）
    allowed = state.awaiting.get(action.player, ())
    if action.type not in allowed:
        raise IllegalAction(
            f"{action.player!r} は今 {action.type!r} を実行できない"
            f"（awaiting={dict(state.awaiting)}）"
        )


# --- 各 Action の処理 --------------------------------------------------------


def _play(reg: HookRegistry, state: GameState, action: PlayAction) -> GameState:
    hand = state.hands[action.player]
    card = _find_card(hand, action.card_id)
    if not reg.can_play(Ctx.from_state(state, action=action, card=card, owner=action.player)):
        raise IllegalAction(f"card_id={action.card_id} は今は出せない")

    state = state.with_awaiting({})  # 手番アクションを消費
    # 効果(前): カード移動前。top は旧トップ
    state = reg.transform(
        ON_BEFORE_PLAY, state, Ctx.from_state(state, action=action, card=card, owner=action.player)
    )
    # カードを手札→捨て山へ（engine 機構）
    hand = state.hands[action.player]
    new_hand = tuple(c for c in hand if c.id != card.id)
    state = state.replace(
        hands={**state.hands, action.player: new_hand},
        discard_pile=state.discard_pile + (card,),
    )
    # 効果(後): top は今出したカード
    state = reg.transform(
        ON_AFTER_PLAY, state, Ctx.from_state(state, action=action, card=card, owner=action.player)
    )
    return _advance_if_idle(reg, state, action.player)


def _draw(reg: HookRegistry, state: GameState, action: DrawAction) -> GameState:
    state = state.with_awaiting({})
    n = state.pending_draw if state.pending_draw > 0 else 1
    state = _draw_cards(state, action.player, n)
    state = state.with_pending_draw(0)
    state = reg.transform(ON_DRAW, state, Ctx.from_state(state, action=action, owner=action.player))
    return _advance_if_idle(reg, state, action.player)


def _choose_color(reg: HookRegistry, state: GameState, action: ChooseColorAction) -> GameState:
    """応答待ちの継続（§3.6）。強制色の確定は rules の on_choose_color が行う（§3.2）。

    forced_color は永続フィールドなので engine は書かない（中立に保つ）。継続の機構
    （awaiting クリア → 継続フック → 手番送り）だけを担う。``action.color`` は ctx.action
    から参照できる。
    """
    state = state.with_awaiting({})
    state = reg.transform(
        ON_CHOOSE_COLOR, state, Ctx.from_state(state, action=action, owner=action.player)
    )
    return _advance_if_idle(reg, state, action.player)


def _declare_uno(reg: HookRegistry, state: GameState, action: DeclareUnoAction) -> GameState:
    # 割り込み（手番を消費しない）: awaiting をクリアせず・手番送りもせず hook だけ回す。
    # 土台では専用効果を持たず、new_game も declare_uno を awaiting に載せないため通常は
    # _check_accepted で弾かれる（受理集合を広げるローカルルールが載せて初めて到達）。
    state = reg.transform(
        ON_DECLARE_UNO, state, Ctx.from_state(state, action=action, owner=action.player)
    )
    return state


def _reset(reg: HookRegistry, state: GameState, action: ResetAction) -> GameState:
    """盤面を作り直す（§8）。新しい seed は RNG ストリームから決定的に引く。"""
    seed, state = state.with_rng(lambda rng: rng.getrandbits(32))
    return GameState.new_game(state.players, seed)


_DISPATCH = {
    PlayAction.type: _play,
    DrawAction.type: _draw,
    ChooseColorAction.type: _choose_color,
    DeclareUnoAction.type: _declare_uno,
    ResetAction.type: _reset,
}


# --- 機構ヘルパ --------------------------------------------------------------


def _find_card(hand: tuple[CardInstance, ...], card_id: int) -> CardInstance:
    for c in hand:
        if c.id == card_id:
            return c
    raise IllegalAction(f"手札に card_id={card_id} が無い")


def _advance_if_idle(reg: HookRegistry, state: GameState, actor: str) -> GameState:
    """終局でも応答待ちでもなければ、ターン終了フックを回して既定の手番送りを行う。

    区別（§3.6）:
    - ``winner`` が立っていれば終局 → 何もしない。
    - ``awaiting`` が非空なら応答待ちで停止（色選択待ち・スキップの自ターン保持など）。
    - どちらでもない（idle）なら ``on_turn_end`` を回し、その結果でも終局/停止でなければ
      二人対戦の既定手番送り（相手へ、受理集合を ``[play, draw]``）を行う。
    """
    if state.winner is not None:
        return state
    if state.awaiting:
        return state
    # ターン終了フック（勝敗判定・ペナルティ等を rules が差し込める配線, §3.2）
    state = reg.transform(ON_TURN_END, state, Ctx.from_state(state, owner=actor))
    if state.winner is not None or state.awaiting:
        return state
    other = state.other_player(actor)
    return state.with_current_player(other).with_awaiting({other: STANDARD_TURN_ACTIONS})


def _draw_cards(state: GameState, player: str, n: int) -> GameState:
    """山の上から最大 n 枚引いて手札へ。山切れ時は捨て山（トップ以外）を再シャッフル。"""
    # 注: デッキ枯渇で n 枚引ききれなくても呼び出し側は pending_draw を 0 にする（土台の
    # 割り切り。二人・108枚では稀。引けなかった罰則を残す挙動が要るなら rules で拡張する）。
    for _ in range(n):
        state = _refill_if_empty(state)
        if not state.draw_pile:
            break  # 補充不能（デッキ枯渇）: 引けるだけで打ち切る
        card = state.draw_pile[-1]
        state = state.replace(
            draw_pile=state.draw_pile[:-1],
            hands={**state.hands, player: state.hands[player] + (card,)},
        )
    return state


def _refill_if_empty(state: GameState) -> GameState:
    """山札が空なら捨て山のトップ以外を注入 RNG で再シャッフルして山札に戻す（§3.5）。"""
    if state.draw_pile:
        return state
    if len(state.discard_pile) <= 1:
        return state  # 戻せる札が無い
    top = state.discard_pile[-1]
    rest = list(state.discard_pile[:-1])

    def shuffle(rng):
        rng.shuffle(rest)
        return tuple(rest)

    new_pile, state = state.with_rng(shuffle)
    return state.replace(draw_pile=new_pile, discard_pile=(top,))


__all__ = [
    "EngineError",
    "IllegalAction",
    "STANDARD_TURN_ACTIONS",
    "ON_DECLARE_UNO",
    "apply_action",
    "apply_actions",
]
