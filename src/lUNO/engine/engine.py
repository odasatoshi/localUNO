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
    ChallengeUnoAction,
    ChooseColorAction,
    DeclareUnoAction,
    DrawAction,
    PassAction,
    PlayAction,
    ResetAction,
)
from .cards import CardInstance
from .hooks import (
    CAN_STACK,
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
ON_CHALLENGE_UNO = "on_challenge_uno"

# awaiting ゲートを介さず常時受理する割り込み・メタ操作（§8 / house-rules §6）。
# reset は盤面再構築、declare_uno / challenge_uno は「ボタン」的にいつでも押せる
# （有効性・ペナルティの判定は rules のフックが担う）。
_ALWAYS_ACCEPTED = frozenset(
    {ResetAction.type, DeclareUnoAction.type, ChallengeUnoAction.type}
)


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
    if action.type in _ALWAYS_ACCEPTED:
        return  # reset / declare_uno / challenge_uno は awaiting を介さず常時受理
    allowed = state.awaiting.get(action.player, ())
    if action.type not in allowed:
        raise IllegalAction(
            f"{action.player!r} は今 {action.type!r} を実行できない"
            f"（awaiting={dict(state.awaiting)}）"
        )


# --- 各 Action の処理 --------------------------------------------------------


def _play(reg: HookRegistry, state: GameState, action: PlayAction) -> GameState:
    hand = state.hands[action.player]
    played = tuple(_find_card(hand, cid) for cid in action.card_ids)  # 出す順（末尾=トップ）
    lead = played[0]
    who = action.player

    # 先頭カードは場に合法か（can_play, §3.4）
    if not reg.can_play(_pctx(state, action, lead, played, who)):
        raise IllegalAction(f"card_id={lead.id} は今は出せない")
    # 複数枚出しは can_stack ゲート（既定 False＝単数のみ）。ルールが同数字/同記号を許可する
    if len(played) > 1 and not reg.reduce(CAN_STACK, _pctx(state, action, lead, played, who)):
        raise IllegalAction("複数枚出しは許可されていない")

    state = state.with_awaiting({})  # 手番アクションを消費
    # 効果(前): カード移動前。top は旧トップ。card は先頭カード。
    state = reg.transform(ON_BEFORE_PLAY, state, _pctx(state, action, lead, played, who))
    # カード群を手札→捨て山へ（出した順に積む、末尾=トップ）
    hand = state.hands[who]
    played_ids = {c.id for c in played}
    new_hand = tuple(c for c in hand if c.id not in played_ids)
    state = state.replace(
        hands={**state.hands, who: new_hand},
        discard_pile=state.discard_pile + played,
    )
    # 効果(後): top は最後に出したカード。card はトップ、played_cards は全群。
    state = reg.transform(ON_AFTER_PLAY, state, _pctx(state, action, played[-1], played, who))
    return _advance_if_idle(reg, state, who)


def _pctx(
    state: GameState,
    action: PlayAction,
    card: CardInstance,
    played: tuple[CardInstance, ...],
    who: str,
) -> Ctx:
    """play 用の Ctx（card＝注目カード, played_cards＝出す群）。"""
    return Ctx.from_state(state, action=action, card=card, played_cards=played, owner=who)


def _draw(reg: HookRegistry, state: GameState, action: DrawAction) -> GameState:
    state = state.with_awaiting({})
    forced = state.pending_draw > 0  # Draw2/4 の累積による強制ドローか（自主ドローと区別）
    n = state.pending_draw if forced else 1
    before = len(state.hands[action.player])
    state = draw_cards(state, action.player, n)
    state = state.with_pending_draw(0)
    # ドロー後プレイ（#40）判定用に「自主ドローで引いた札」だけを渡す。強制ドローは None
    # とし、山切れで1枚しか引けなくても自主ドローと誤判定させない。
    drawn = None if forced else state.hands[action.player][before:]
    state = reg.transform(
        ON_DRAW,
        state,
        Ctx.from_state(state, action=action, drawn_cards=drawn, owner=action.player),
    )
    return _advance_if_idle(reg, state, action.player)


def _pass(reg: HookRegistry, state: GameState, action: PassAction) -> GameState:
    """ドロー後の「出さずにパス」（house-rules §7）。受理は awaiting 依存（ルールが載せる）。

    ドロー後マーカーを消し、既定の手番送りを行う。
    """
    state = state.with_drawn_card_id(None).with_awaiting({})
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
    # 常時受理（_ALWAYS_ACCEPTED）で「UNO!」ボタンをいつでも押せる。宣言の有効性
    # （1枚時のみ有効・枚数増でクリア）は rules の on_declare_uno が判定する（house-rules §6）。
    return reg.transform(
        ON_DECLARE_UNO, state, Ctx.from_state(state, action=action, owner=action.player)
    )


def _challenge_uno(reg: HookRegistry, state: GameState, action: ChallengeUnoAction) -> GameState:
    # 割り込み（手番を消費しない）: 「UNO言ってない!」ボタン。指摘の成否とペナルティ
    # （成功=相手2枚 / 誤爆=自分2枚, house-rules §6）は rules の on_challenge_uno が担う。
    return reg.transform(
        ON_CHALLENGE_UNO, state, Ctx.from_state(state, action=action, owner=action.player)
    )


def _reset(reg: HookRegistry, state: GameState, action: ResetAction) -> GameState:
    """盤面を作り直す（§8）。新しい seed は RNG ストリームから決定的に引く。

    エンジンはルール非依存なので場札めくり（setup）は行わず ``new_game`` で素の初期状態を
    返す。標準ルールの場札めくりを伴う再戦が要る経路（server/session.py）は ResetAction を
    横取りして rules の setup を呼ぶ。両者の差異に注意。
    """
    seed, state = state.with_rng(lambda rng: rng.getrandbits(32))
    return GameState.new_game(state.players, seed)


_DISPATCH = {
    PlayAction.type: _play,
    DrawAction.type: _draw,
    ChooseColorAction.type: _choose_color,
    DeclareUnoAction.type: _declare_uno,
    ChallengeUnoAction.type: _challenge_uno,
    PassAction.type: _pass,
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
    - ``winner`` が立っている／``is_draw`` なら終局 → 何もしない。
    - ``awaiting`` が非空なら応答待ちで停止（色選択待ち・スキップの自ターン保持など）。
    - どちらでもない（idle）なら ``on_turn_end`` を回し、その結果でも終局/停止でなければ
      二人対戦の既定手番送り（相手へ、受理集合を ``[play, draw]``）を行う。
    """
    if state.winner is not None or state.is_draw:
        return state
    if state.awaiting:
        return state
    # ターン終了フック（勝敗判定・山切れ引き分け・ペナルティ等を rules が差し込める配線, §3.2）
    state = reg.transform(ON_TURN_END, state, Ctx.from_state(state, owner=actor))
    if state.winner is not None or state.is_draw or state.awaiting:
        return state
    other = state.other_player(actor)
    return state.with_current_player(other).with_awaiting({other: STANDARD_TURN_ACTIONS})


def draw_cards(state: GameState, player: str, n: int) -> GameState:
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
    "ON_CHALLENGE_UNO",
    "apply_action",
    "apply_actions",
    "draw_cards",
]
