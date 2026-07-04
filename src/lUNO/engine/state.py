"""権威状態 GameState と、視界フィルタ済み送信物 PlayerView（spec.md §3.1/§3.6/§5）。

- :class:`GameState` は両手札・山札順序・捨て山・手番・強制色・方向・累積ドロー枚数・
  RNG 状態・``awaiting`` を保持する権威状態（§3.1）。
- ``awaiting`` は ``{player_id -> (allowed_action_types, ...)}`` のプレイヤー別マップ
  （手番外・割り込みを表現できる最小設定、§3.6）。
- 永続フィールド（``pending_draw`` / ``forced_color`` / ``direction`` /
  ``current_player`` など）は **GameState が唯一のオーナー**。GameState は不変で、
  書き換えは ``with_*`` / :func:`dataclasses.replace` が新しいインスタンスを返す形に
  限る。値リデューサ型フックはこれらを書かない（§3.2）。
- 非決定要素は注入 RNG に閉じ込める（§3.5）。RNG は**不変な状態タプル**
  ``rng_state`` として保持し、乱数消費は :meth:`GameState.with_rng` を通して
  「複製して進めた新 state を返す」純関数的経路に限る。これにより旧 state の RNG
  ストリームは前進せず、「同じ seed＋同じ Action 列 → 同じ GameState」を保つ。
- :func:`player_view` は ``GameState -> PlayerView`` のホワイトリスト**純関数**。山札の
  順序・中身、相手手札の中身、RNG は載せない（§5）。

エンジンの純粋性を保つため、本モジュールはネットワーク・描画・画像生成に一切依存
しない（原則2）。
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeVar

from .actions import DrawAction, PlayAction
from .cards import CardInstance, Color, Deck

T = TypeVar("T")


def _readonly(mapping: Mapping) -> MappingProxyType:
    """dict を読み取り専用ビューへ正規化する（frozen のすり抜け防止）。"""
    return MappingProxyType(dict(mapping))


def _card_to_dict(inst: CardInstance) -> dict:
    """CardInstance を送信用 dict に落とす（描画に必要な素の属性のみ）。"""
    return {
        "id": inst.id,
        "color": str(inst.color) if inst.color is not None else None,
        "symbol": inst.symbol,
        "label": inst.card_type.label,
        "image_key": inst.card_type.image_key,
    }


@dataclass(frozen=True)
class GameState:
    """サーバが持つ権威状態の全体（spec §3.1）。

    不変（frozen）に保ち、変更は ``with_*`` / :func:`dataclasses.replace` で新しい
    インスタンスを返す。可変 dict の in-place 変更を防ぐため ``hands`` / ``awaiting``
    は読み取り専用ビューへ正規化する。RNG は不変な ``rng_state`` タプルで保持し、
    等価判定にも含める（RNG が発散した2状態を ``==`` が等しいと誤判定しない）。
    捨て山・山札は末尾を「上」とする（Deck 規約に合わせる）。
    """

    hands: Mapping[str, tuple[CardInstance, ...]]
    draw_pile: tuple[CardInstance, ...]
    discard_pile: tuple[CardInstance, ...]
    current_player: str
    rng_state: tuple = field(repr=False)
    forced_color: Color | None = None
    direction: int = 1
    pending_draw: int = 0
    awaiting: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # frozen をすり抜ける可変 dict を読み取り専用ビューへ（§3.2 の所有権を担保）
        object.__setattr__(self, "hands", _readonly(self.hands))
        object.__setattr__(self, "awaiting", _readonly(self.awaiting))

    # --- 参照系（読み取り専用の便宜） -------------------------------------

    @property
    def players(self) -> tuple[str, ...]:
        return tuple(self.hands.keys())

    def top_of_pile(self) -> CardInstance | None:
        """捨て山トップ（無ければ None）。"""
        return self.discard_pile[-1] if self.discard_pile else None

    def other_player(self, player_id: str) -> str:
        """二人対戦での相手プレイヤー ID を返す。"""
        others = [p for p in self.hands if p != player_id]
        if len(others) != 1:
            raise ValueError("other_player は二人対戦専用です")
        return others[0]

    # --- 永続フィールドの不変更新（GameState が唯一のオーナー、§3.2） ------

    def replace(self, **changes: object) -> GameState:
        """一部フィールドを差し替えた新しい GameState を返す。"""
        return dataclasses.replace(self, **changes)

    def with_current_player(self, player_id: str) -> GameState:
        return self.replace(current_player=player_id)

    def with_forced_color(self, color: Color | None) -> GameState:
        return self.replace(forced_color=color)

    def with_direction(self, direction: int) -> GameState:
        return self.replace(direction=direction)

    def with_pending_draw(self, pending_draw: int) -> GameState:
        return self.replace(pending_draw=pending_draw)

    def with_awaiting(self, awaiting: Mapping[str, Iterable[str]]) -> GameState:
        """受理可能アクションのマップを差し替える（値はタプル化して不変化）。"""
        normalized = {pid: tuple(actions) for pid, actions in awaiting.items()}
        return self.replace(awaiting=normalized)

    # --- RNG の関数的取り扱い（§3.5） ------------------------------------

    def with_rng(self, fn: Callable[[random.Random], T]) -> tuple[T, GameState]:
        """RNG 状態を復元した Random で ``fn`` を実行し、``(結果, 進めた新 state)`` を返す。

        入力 state（``self``）の ``rng_state`` は不変タプルなので前進しない。乱数を
        消費する state トランスフォーマは必ずこの経路を通すことで、純粋性と
        「同じ seed → 同じ結果」の再現性を両立する（§3.5）。
        """
        rng = random.Random()
        rng.setstate(self.rng_state)
        result = fn(rng)
        return result, self.replace(rng_state=rng.getstate())

    # --- 初期状態の生成（RNG 注入で決定的、§3.5） -------------------------

    @classmethod
    def new_game(
        cls,
        player_ids: Sequence[str],
        seed: int,
        hand_size: int = 7,
    ) -> GameState:
        """標準デッキを注入 RNG でシャッフルし配札した初期 GameState。

        同じ ``seed`` なら配札・RNG 状態まで決定的（§3.5）。配りは実物 UNO の交互配り
        ではなく player 順にまとめて配るが、決定性さえあれば engine 的に無害。ゲーム
        開始時の場札めくりや効果適用は engine(#10) の責務とし、捨て山は空のままにする。
        """
        if len(player_ids) != 2:
            raise ValueError("二人対戦専用です")
        rng = random.Random(seed)
        deck = Deck.standard()
        deck.shuffle(rng)
        hands = {pid: tuple(deck.draw_many(hand_size)) for pid in player_ids}
        draw_pile = tuple(deck.cards)  # 残り（末尾が山の上）
        first = player_ids[0]
        awaiting = {first: (PlayAction.type, DrawAction.type)}
        return cls(
            hands=hands,
            draw_pile=draw_pile,
            discard_pile=(),
            current_player=first,
            rng_state=rng.getstate(),
            awaiting=awaiting,
        )


@dataclass(frozen=True)
class PlayerView:
    """特定プレイヤー向けに視界フィルタした送信用スナップショット（spec §5）。

    ホワイトリスト方式。ここに載っていないもの（山札順序・相手手札中身・RNG）は
    構造上そもそも保持しない。可変 dict は読み取り専用ビューへ正規化する。
    """

    you: str
    your_hand: tuple[CardInstance, ...]
    hand_counts: Mapping[str, int]
    draw_count: int
    top_of_pile: CardInstance | None
    forced_color: Color | None
    direction: int
    pending_draw: int
    current_player: str
    awaiting: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "hand_counts", _readonly(self.hand_counts))
        object.__setattr__(self, "awaiting", _readonly(self.awaiting))

    def to_dict(self) -> dict:
        """クライアント送信用の JSON 互換 dict。"""
        top = self.top_of_pile
        return {
            "you": self.you,
            "your_hand": [_card_to_dict(c) for c in self.your_hand],
            "hand_counts": dict(self.hand_counts),
            "draw_count": self.draw_count,
            "top_of_pile": _card_to_dict(top) if top is not None else None,
            "forced_color": str(self.forced_color) if self.forced_color is not None else None,
            "direction": self.direction,
            "pending_draw": self.pending_draw,
            "current_player": self.current_player,
            "awaiting": {pid: list(actions) for pid, actions in self.awaiting.items()},
        }


def player_view(state: GameState, player_id: str) -> PlayerView:
    """``GameState -> PlayerView`` のホワイトリスト純関数（spec §5）。

    公開するのは: 捨て山トップ・強制色・方向・累積ドロー・手番・awaiting・各手札枚数・
    山札残り枚数、および**本人の手札の中身のみ**。山札の順序/中身、相手手札の中身、
    RNG は載せない（デフォルト秘匿）。漏れ防止の本質は「PlayerView が秘匿対象の
    フィールドを構造的に持たない」ホワイトリスト設計にある。
    """
    if player_id not in state.hands:
        raise ValueError(f"未知のプレイヤー: {player_id!r}")
    return PlayerView(
        you=player_id,
        your_hand=state.hands[player_id],
        hand_counts={pid: len(hand) for pid, hand in state.hands.items()},
        draw_count=len(state.draw_pile),
        top_of_pile=state.top_of_pile(),
        forced_color=state.forced_color,
        direction=state.direction,
        pending_draw=state.pending_draw,
        current_player=state.current_player,
        awaiting=dict(state.awaiting),
    )


__all__ = [
    "GameState",
    "PlayerView",
    "player_view",
]
