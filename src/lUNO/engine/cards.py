"""カードのデータモデル: CardType(定義) / CardInstance(個体) / Deck。

spec.md §4, §3.5 準拠:

- CardType は「素の属性のみ」を持ち、判定ロジックは持たない（§4.2）。一致判定は
  can_play フックに閉じ込める。CardType の同一性（完全一致）は全属性の一致で表す。
- CardInstance は CardType 参照 + 一意 ID を持ち、同一 CardType の複数個体を ID で
  区別する（§4.1）。山札・手札・捨て山にはこのインスタンスが並ぶ。
- Deck のシャッフル・配札は注入 RNG(random.Random) を通す（§3.5）。エンジン内部で
  random のグローバル状態やシステム時刻には触れない。同じシード＋同じ操作列 →
  同じ結果、を保証し、純粋性とテスト再現性を両立する。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum


class Color(StrEnum):
    """色付きカードの色。ワイルド系は色を持たない（CardType.color=None）。"""

    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"


# 記号（symbol）。数字カードは "0".."9"、以下は記号カード。
# 文字列で表すことで、ローカルルールが新しい記号の CardType を足しやすくする。
SKIP = "skip"
REVERSE = "reverse"
DRAW2 = "draw2"
WILD = "wild"
DRAW4 = "draw4"

COLORS: tuple[Color, ...] = (Color.RED, Color.YELLOW, Color.GREEN, Color.BLUE)


@dataclass(frozen=True)
class CardType:
    """カードの定義。素の属性のみを持ち、判定ロジックは持たない（spec §4.2）。

    色・記号・表示ラベル・画像メタデータ・効果への参照を保持する。frozen dataclass
    なので全属性の一致で等価判定され、ハッシュ可能（画像生成での重複排除に使える）。
    ジャンプインの「完全一致」はこの同一性で自然に表現できる（§4.1）。

    - 色付きカードは ``color`` を持ち、ワイルド系は ``color=None``。
    - ``effect`` は効果への参照。標準ルールは記号(symbol)を見てフックで効果を適用する
      ため通常 None のままだが、ローカルルールが明示参照を持たせる余地を残す。
    """

    symbol: str
    color: Color | None = None
    label: str = ""
    effect: str | None = None

    @property
    def is_wild(self) -> bool:
        """色を持たない（ワイルド系）か。属性由来の区分であり判定ロジックではない。"""
        return self.color is None

    @property
    def image_key(self) -> str:
        """画像メタデータ: カード面を一意に指す安定キー（生成器・命名で使用）。

        カード面は見た目（色・記号）で決まるため、等価判定に含まれる label/effect は
        意図的に無視する（見た目が同じ札は同一画像に畳む）。具体のパス/拡張子など
        命名規約は画像生成 PR(#12) で確定する（spec §12）。
        """
        return f"{self.color.value}_{self.symbol}" if self.color is not None else self.symbol


@dataclass(frozen=True)
class CardInstance:
    """カードの個体。CardType 参照 + 一意 ID を持つ（spec §4.1）。

    同一 CardType の個体でも ``id`` が異なれば別物として区別できる（frozen dataclass
    の等価性は全フィールド一致で判定するため）。``color`` などは CardType へ委譲する
    薄いプロパティで、フックが ``ctx.card.symbol`` のように参照できるようにする。
    """

    card_type: CardType
    id: int

    @property
    def color(self) -> Color | None:
        return self.card_type.color

    @property
    def symbol(self) -> str:
        return self.card_type.symbol

    @property
    def is_wild(self) -> bool:
        return self.card_type.is_wild


@dataclass
class Deck:
    """山札。リスト末尾を「山の上（次に引く札）」とする。

    シャッフル・配札は注入 RNG(random.Random) を通す（spec §3.5）。同じシード＋同じ
    操作列 → 同じ結果、を保証する。GameState 側での不変管理は後続 issue(#8) が担う。
    """

    cards: list[CardInstance] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cards)

    def shuffle(self, rng: random.Random) -> None:
        """注入 RNG でシャッフル（in-place）。random のグローバル状態には触れない。"""
        rng.shuffle(self.cards)

    def draw(self) -> CardInstance:
        """山の上から1枚引く。山が空なら IndexError。"""
        if not self.cards:
            raise IndexError("draw from empty deck")
        return self.cards.pop()

    def draw_many(self, n: int) -> list[CardInstance]:
        """山の上から n 枚引く。引いた順（山の上から）のリストを返す。"""
        if n < 0:
            raise ValueError("n must be >= 0")
        if n > len(self.cards):
            raise ValueError("not enough cards to draw")
        return [self.draw() for _ in range(n)]

    @classmethod
    def standard(cls) -> Deck:
        """標準 UNO の 108 枚デッキ（未シャッフル）。個体 ID は 0 から連番で決定的。"""
        cards: list[CardInstance] = []
        cid = 0
        for card_type, count in standard_deck_composition():
            for _ in range(count):
                cards.append(CardInstance(card_type=card_type, id=cid))
                cid += 1
        return cls(cards)


def standard_deck_composition() -> list[tuple[CardType, int]]:
    """標準 UNO の (CardType, 枚数) 構成（合計 108 枚）。デッキ・一覧の単一ソース。

    色ごとに 0×1・1-9×2・skip/reverse/draw2×2、ワイルド×4・ワイルドドロー4×4。
    """
    comp: list[tuple[CardType, int]] = []
    for color in COLORS:
        comp.append((CardType(symbol="0", color=color, label="0"), 1))
        for n in range(1, 10):
            comp.append((CardType(symbol=str(n), color=color, label=str(n)), 2))
        comp.append((CardType(symbol=SKIP, color=color, label="Skip"), 2))
        comp.append((CardType(symbol=REVERSE, color=color, label="Reverse"), 2))
        comp.append((CardType(symbol=DRAW2, color=color, label="+2"), 2))
    comp.append((CardType(symbol=WILD, color=None, label="Wild"), 4))
    comp.append((CardType(symbol=DRAW4, color=None, label="+4"), 4))
    return comp


def standard_card_types() -> list[CardType]:
    """標準 UNO の CardType 一覧（54 種）。画像生成はこの一覧を走査する（spec §7）。"""
    return [card_type for card_type, _ in standard_deck_composition()]
