"""engine/cards.py の試験（spec.md §4, §3.5 の完了条件を担保）。

- CardType の同一性で「完全一致」判定ができる
- 同一 CardType の複数個体を ID で区別できる
- 固定シードで配札が決定的
"""

from __future__ import annotations

import random

import pytest

from lUNO.engine.cards import (
    COLORS,
    DRAW4,
    WILD,
    CardInstance,
    CardType,
    Color,
    Deck,
    standard_card_types,
    standard_deck_composition,
)

# --- CardType: 同一性（完全一致） --------------------------------------------


def test_cardtype_equality_full_match():
    """同じ属性で作った CardType は等価で、ハッシュも一致する（完全一致判定）。"""
    a = CardType(symbol="7", color=Color.RED, label="7")
    b = CardType(symbol="7", color=Color.RED, label="7")
    assert a == b
    assert hash(a) == hash(b)
    # set/dict のキーとして重複排除できる
    assert len({a, b}) == 1


def test_cardtype_inequality_on_any_attr_diff():
    """色 or 記号が異なれば別 CardType（完全一致しない）。"""
    red7 = CardType(symbol="7", color=Color.RED, label="7")
    blue7 = CardType(symbol="7", color=Color.BLUE, label="7")
    red8 = CardType(symbol="8", color=Color.RED, label="8")
    assert red7 != blue7
    assert red7 != red8


def test_cardtype_is_wild():
    """color=None がワイルド系。色付きは is_wild=False。"""
    wild = CardType(symbol=WILD, color=None, label="Wild")
    red = CardType(symbol="0", color=Color.RED, label="0")
    assert wild.is_wild is True
    assert red.is_wild is False


def test_cardtype_image_key():
    """画像メタキーは色付き=color_symbol、ワイルド=symbol。"""
    assert CardType(symbol="7", color=Color.RED, label="7").image_key == "red_7"
    assert CardType(symbol=DRAW4, color=None, label="+4").image_key == "draw4"


# --- CardInstance: ID による個体区別 ----------------------------------------


def test_instances_distinguished_by_id():
    """同一 CardType でも ID が異なれば別個体。同 ID+同 type なら等価。"""
    ct = CardType(symbol="7", color=Color.RED, label="7")
    a = CardInstance(card_type=ct, id=1)
    b = CardInstance(card_type=ct, id=2)
    c = CardInstance(card_type=ct, id=1)
    assert a != b  # 同じ赤7でも ID で区別できる
    assert a == c
    assert a.card_type == b.card_type  # 型としては同一


def test_instance_delegates_attrs():
    """CardInstance は色・記号・is_wild を CardType へ委譲する。"""
    ct = CardType(symbol="7", color=Color.RED, label="7")
    inst = CardInstance(card_type=ct, id=0)
    assert inst.color == Color.RED
    assert inst.symbol == "7"
    assert inst.is_wild is False


# --- 標準デッキ・CardType 一覧 -----------------------------------------------


def test_standard_card_types_count():
    """標準 UNO の CardType は 54 種で、各々一意。"""
    types = standard_card_types()
    assert len(types) == 54
    assert len(set(types)) == 54


def test_standard_deck_composition_total():
    """標準デッキの枚数合計は 108 枚。"""
    total = sum(count for _, count in standard_deck_composition())
    assert total == 108


def test_standard_deck_instances_and_unique_ids():
    """Deck.standard は 108 個体で ID が 0..107 の一意連番。"""
    deck = Deck.standard()
    assert len(deck) == 108
    ids = [c.id for c in deck.cards]
    assert len(set(ids)) == 108
    assert sorted(ids) == list(range(108))


def test_standard_deck_per_color_counts():
    """各色 25 枚（0×1 + 1-9×2 + 記号3種×2）。"""
    deck = Deck.standard()
    for color in COLORS:
        assert sum(1 for c in deck.cards if c.color == color) == 25
    assert sum(1 for c in deck.cards if c.is_wild) == 8


# --- Deck: 注入 RNG による決定的シャッフル・配札 ------------------------------


def _deal(seed: int, hand_size: int = 7):
    deck = Deck.standard()
    deck.shuffle(random.Random(seed))
    p1 = deck.draw_many(hand_size)
    p2 = deck.draw_many(hand_size)
    return p1, p2, deck


def test_deal_deterministic_with_fixed_seed():
    """同一シードなら配札（手札の個体順）とデッキ残が完全一致する。"""
    p1a, p2a, da = _deal(42)
    p1b, p2b, db = _deal(42)
    assert [c.id for c in p1a] == [c.id for c in p1b]
    assert [c.id for c in p2a] == [c.id for c in p2b]
    assert [c.id for c in da.cards] == [c.id for c in db.cards]
    assert len(da) == 108 - 14


def test_deal_differs_with_different_seed():
    """異なるシードでは配布順が変わる（グローバル random に依存しない証左）。"""
    p1a, _, _ = _deal(1)
    p1b, _, _ = _deal(2)
    assert [c.id for c in p1a] != [c.id for c in p1b]


def test_shuffle_uses_injected_rng_only():
    """同じ Random シードで shuffle すれば、間に global random を回しても不変。"""
    d1 = Deck.standard()
    d1.shuffle(random.Random(7))
    random.random()  # global 状態を進めても影響しないことを確認
    d2 = Deck.standard()
    d2.shuffle(random.Random(7))
    assert [c.id for c in d1.cards] == [c.id for c in d2.cards]


def test_draw_from_empty_raises():
    empty = Deck()
    with pytest.raises(IndexError):
        empty.draw()


def test_draw_many_not_enough_raises():
    deck = Deck.standard()
    with pytest.raises(ValueError):
        deck.draw_many(109)


def test_draw_many_negative_raises():
    deck = Deck.standard()
    with pytest.raises(ValueError):
        deck.draw_many(-1)


def test_draw_many_zero_returns_empty():
    deck = Deck.standard()
    assert deck.draw_many(0) == []
    assert len(deck) == 108


def test_draw_many_failure_leaves_deck_intact():
    """事前チェックで弾くため、失敗時に部分的に引く副作用がない。"""
    deck = Deck.standard()
    with pytest.raises(ValueError):
        deck.draw_many(109)
    assert len(deck) == 108


def test_effect_participates_in_equality():
    """effect フィールドは等価判定に効く（効果参照の余地を退行から守る）。"""
    a = CardType(symbol="7", color=Color.RED, label="7", effect="foo")
    b = CardType(symbol="7", color=Color.RED, label="7", effect="bar")
    same = CardType(symbol="7", color=Color.RED, label="7", effect="foo")
    assert a != b
    assert a == same
