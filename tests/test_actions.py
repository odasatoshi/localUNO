"""engine/actions.py の試験（spec.md §3.1 の完了条件を担保）。

- 各 Action の生成・シリアライズ/デシリアライズ
- 不正 Action を弾く
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import ClassVar

import pytest

from lUNO.engine.actions import (
    Action,
    ActionError,
    ChallengeUnoAction,
    ChooseColorAction,
    DeclareUnoAction,
    DrawAction,
    PlayAction,
    ResetAction,
    coerce_card_id,
    parse,
    register,
)
from lUNO.engine.cards import Color

ALL_ACTIONS = [
    PlayAction(player="p1", card_ids=(5,)),
    PlayAction(player="p1", card_ids=(5, 7, 9)),  # 複数枚出し
    DrawAction(player="p1"),
    ChooseColorAction(player="p1", color=Color.RED),
    DeclareUnoAction(player="p2"),
    ChallengeUnoAction(player="p2"),
    ResetAction(player="p1"),
]


# --- 生成・往復（round-trip） -----------------------------------------------


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_roundtrip_dict(action: Action):
    """to_dict → parse で元に戻る。"""
    assert parse(action.to_dict()) == action


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_roundtrip_json(action: Action):
    """to_json → parse で元に戻る。"""
    assert parse(action.to_json()) == action


def test_to_dict_shape():
    """to_dict は type と各フィールドを含む（card_ids は list で出力）。"""
    assert PlayAction(player="p1", card_ids=(5, 7)).to_dict() == {
        "type": "play",
        "player": "p1",
        "card_ids": [5, 7],
    }
    assert DrawAction(player="p2").to_dict() == {"type": "draw", "player": "p2"}


def test_play_card_id_property_is_first():
    """単数プレイの後方互換アクセサ card_id は先頭カード。"""
    assert PlayAction(player="p1", card_ids=(5, 7)).card_id == 5


def test_reject_empty_card_ids():
    with pytest.raises(ActionError):
        PlayAction(player="p1", card_ids=())


def test_reject_duplicate_card_ids():
    with pytest.raises(ActionError):
        parse({"type": "play", "player": "p1", "card_ids": [5, 5]})


def test_choose_color_serializes_color_as_str():
    """color は素の文字列で入出力される。"""
    d = ChooseColorAction(player="p1", color=Color.BLUE).to_dict()
    assert d["color"] == "blue"
    assert isinstance(d["color"], str)
    restored = parse(d)
    assert isinstance(restored, ChooseColorAction)
    assert restored.color == Color.BLUE


def test_parse_accepts_json_string():
    a = parse('{"type": "play", "player": "p1", "card_ids": [7]}')
    assert a == PlayAction(player="p1", card_ids=(7,))


def test_parse_accepts_bytes():
    a = parse(b'{"type": "draw", "player": "p1"}')
    assert a == DrawAction(player="p1")


def test_parse_does_not_mutate_input():
    raw = {"type": "draw", "player": "p1"}
    parse(raw)
    assert raw == {"type": "draw", "player": "p1"}  # type が pop されて残らないこと


def test_distinct_types_not_equal():
    """フィールドが同形でも別種別は等価にならない。"""
    assert DrawAction(player="p1") != ResetAction(player="p1")


# --- 不正 Action を弾く ------------------------------------------------------


def test_reject_unknown_type():
    with pytest.raises(ActionError):
        parse({"type": "no_such", "player": "p1"})


def test_reject_missing_type():
    with pytest.raises(ActionError):
        parse({"player": "p1"})


def test_reject_missing_required_field():
    with pytest.raises(ActionError):
        parse({"type": "play", "player": "p1"})  # card_ids 欠落


def test_reject_extra_field():
    with pytest.raises(ActionError):
        parse({"type": "draw", "player": "p1", "bogus": 1})


def test_reject_wrong_type_card_ids():
    with pytest.raises(ActionError):
        parse({"type": "play", "player": "p1", "card_ids": "5"})  # 文字列は不可
    with pytest.raises(ActionError):
        parse({"type": "play", "player": "p1", "card_ids": [5, "x"]})  # 要素が非int


def test_reject_bool_card_id():
    """bool は int のサブクラスだが card_ids の要素としては弾く。"""
    with pytest.raises(ActionError):
        parse({"type": "play", "player": "p1", "card_ids": [True]})


def test_reject_invalid_color():
    with pytest.raises(ActionError):
        parse({"type": "choose_color", "player": "p1", "color": "pink"})


def test_reject_non_string_color():
    with pytest.raises(ActionError):
        parse({"type": "choose_color", "player": "p1", "color": None})


def test_reject_empty_player():
    with pytest.raises(ActionError):
        parse({"type": "draw", "player": ""})


def test_reject_non_string_player():
    with pytest.raises(ActionError):
        parse({"type": "draw", "player": 1})


def test_reject_non_object_payload():
    with pytest.raises(ActionError):
        parse(json.dumps([1, 2, 3]))


def test_reject_invalid_json():
    with pytest.raises(ActionError):
        parse("{not json")


# --- 拡張性・登録まわり ------------------------------------------------------


def test_abstract_base_cannot_be_instantiated():
    """基底 Action は type 未設定なので直接生成できない。"""
    with pytest.raises(ActionError):
        Action(player="p1")


def test_duplicate_type_registration_rejected():
    """同一 type の二重登録は弾く。"""
    with pytest.raises(ValueError):

        @register
        @dataclass(frozen=True)
        class DupPlay(Action):
            type: ClassVar[str] = "play"  # 既存と重複


def test_new_action_extends_without_central_edits():
    """新 Action は 1 クラス内（field metadata のバリデータ）で完結して足せる。"""

    @register
    @dataclass(frozen=True)
    class BetAction(Action):
        amount: int = field(metadata={"coerce": coerce_card_id})
        type: ClassVar[str] = "bet"

    try:
        parsed = parse({"type": "bet", "player": "p1", "amount": 10})
        assert parsed == BetAction(player="p1", amount=10)
        assert parsed.to_dict() == {"type": "bet", "player": "p1", "amount": 10}
        with pytest.raises(ActionError):
            parse({"type": "bet", "player": "p1", "amount": "x"})
    finally:
        # レジストリ汚染を残さない（他テストへの影響防止）
        from lUNO.engine.actions import _REGISTRY

        _REGISTRY.pop("bet", None)
