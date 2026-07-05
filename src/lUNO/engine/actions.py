"""プレイヤー入力を表す Action 型（spec.md §3.1）。

フロントから JSON で届く前提で、次を提供する:

- 各 Action を frozen dataclass として定義（`play` / `draw` / `choose_color` /
  `declare_uno` / `reset`）。
- ``to_dict`` / ``to_json`` によるシリアライズ。
- :func:`parse` による JSON/dict からの復元と検証（未知種別・欠落/余剰フィールド・
  型不一致を :class:`ActionError` で弾く）。

**拡張のしかた**: 新しい Action は基底 :class:`Action` を継承し、ClassVar ``type`` を
設定し、各フィールドに ``field(metadata={"coerce": ...})`` でバリデータを添えて
:func:`register` するだけで足せる。検証は各クラス内で完結し、engine 中央の共通関数を
編集する必要はない（spec の「ルールは rules/ 内で完結」思想に沿う）。

Action 層が行うのは**構造検証のみ**（種別・フィールドの有無・スカラ型）。``card_id`` の
実在性など state と突き合わせる検証は engine（#8）の責務とし、ここでは行わない。
エンジンの純粋性を保つため、ネットワーク・描画・GameState には依存しない。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from typing import ClassVar

from .cards import Color


class ActionError(ValueError):
    """不正な Action。未知の種別・フィールド欠落/余剰・型不一致などで送出する。"""


_REGISTRY: dict[str, type[Action]] = {}


def register(cls: type[Action]) -> type[Action]:
    """Action サブクラスを ``type`` 名で登録する（:func:`parse` の逆引き表）。

    デコレータとして使う。rules/ 側から新 Action を登録する公開 API でもある。
    """
    if not cls.type:
        raise ValueError("Action サブクラスは ClassVar `type` を設定すること")
    if cls.type in _REGISTRY:
        raise ValueError(f"Action type が重複: {cls.type!r}")
    _REGISTRY[cls.type] = cls
    return cls


# --- フィールドバリデータ（field metadata から参照される再利用可能な変換） --------


def coerce_player(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ActionError("player は非空文字列であること")
    return value


def coerce_card_id(value: object) -> int:
    # bool は int のサブクラスなので明示的に除外する
    if isinstance(value, bool) or not isinstance(value, int):
        raise ActionError("card_id は int であること")
    return value


def coerce_card_ids(value: object) -> tuple[int, ...]:
    """複数枚出し用。int の列（list/tuple）を検証してタプル化する。

    非空・要素は int（bool 除外）・重複なし。出す順を保持する（末尾がトップ）。
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ActionError("card_ids は int のリストであること")
    ids = tuple(coerce_card_id(v) for v in value)
    if not ids:
        raise ActionError("card_ids は空にできない")
    if len(set(ids)) != len(ids):
        raise ActionError("card_ids に重複がある")
    return ids


def coerce_rule_ids(value: object) -> tuple[str, ...]:
    """新規ゲームの有効ルール id 列を検証してタプル化する（#85）。

    非空文字列の列（list/tuple）。順序を保って重複を除く（この順序は入力の正規化用で、
    フック合成順は下流の RULE_CATALOG 順が権威）。空リストは許容する（＝ハウスルール無し・
    標準のみの対戦。standard は必須で常に有効）。カタログに実在するかの意味検証はサーバ層
    （Session）の責務（engine は rules を import しない）。
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ActionError("enabled_rule_ids は文字列のリストであること")
    seen: set[str] = set()
    ids: list[str] = []
    for v in value:
        if not isinstance(v, str) or not v:
            raise ActionError("enabled_rule_ids の要素は非空文字列であること")
        if v not in seen:
            seen.add(v)
            ids.append(v)
    return tuple(ids)


def coerce_color(value: object) -> Color:
    try:
        return Color(value)
    except ValueError as e:
        raise ActionError(f"不正な color: {value!r}") from e


def _encode(value: object) -> object:
    """to_dict 用の変換。Color は素の文字列、タプルは JSON 自然な list に落とす。"""
    if isinstance(value, Color):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


@dataclass(frozen=True)
class Action:
    """プレイヤー入力の基底。全 Action は発行者 ``player`` を持つ（spec §3.1）。

    抽象基底であり直接生成しない。サブクラスは ClassVar ``type`` に一意な種別名を
    設定し :func:`register` する。frozen dataclass なので値等価・ハッシュ可能。
    """

    player: str = field(metadata={"coerce": coerce_player})
    type: ClassVar[str] = ""

    def __post_init__(self) -> None:
        if not self.type:
            raise ActionError("Action は抽象基底です。具体サブクラスを使うこと")

    def to_dict(self) -> dict:
        """``{"type": ..., 各フィールド}`` の dict へ変換する。"""
        payload: dict = {"type": self.type}
        for f in fields(self):
            payload[f.name] = _encode(getattr(self, f.name))
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def _from_payload(cls, data: dict) -> Action:
        """``type`` を除いた dict からインスタンスを構築・検証する。

        各フィールドは自身の ``metadata["coerce"]`` で検証する（クラス内で完結）。
        """
        expected = [f.name for f in fields(cls)]
        expected_set = set(expected)
        keys = set(data.keys())
        missing = expected_set - keys
        if missing:
            raise ActionError(f"{cls.type}: フィールド欠落 {sorted(missing)}")
        extra = keys - expected_set
        if extra:
            raise ActionError(f"{cls.type}: 余剰フィールド {sorted(extra)}")
        kwargs = {}
        for f in fields(cls):
            coerce: Callable[[object], object] | None = f.metadata.get("coerce")
            if coerce is None:
                raise ActionError(f"{cls.type}: フィールド {f.name} にバリデータ未設定")
            kwargs[f.name] = coerce(data[f.name])
        return cls(**kwargs)


@register
@dataclass(frozen=True)
class PlayAction(Action):
    """手札の1枚以上（同時出し）を場に出す。

    ``card_ids`` は出す順（末尾がトップ）。単数プレイは要素1個のタプル。複数枚出しの
    合法性（同数字/同記号など）は rules の ``can_play`` / ``can_stack`` フックが判定する。
    """

    card_ids: tuple[int, ...] = field(metadata={"coerce": coerce_card_ids})
    type: ClassVar[str] = "play"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.card_ids, tuple) or not self.card_ids:
            raise ActionError("card_ids は非空タプルであること")
        # 重複は物理カードの複製（捨て山に同一カードが2度積まれる）を招くため型レベルで
        # 弾く。parse 経路(coerce_card_ids)だけでなく直接構築でも不変条件を担保する。
        if len(set(self.card_ids)) != len(self.card_ids):
            raise ActionError("card_ids に重複がある")

    @property
    def card_id(self) -> int:
        """先頭カード ID（単数プレイの後方互換アクセサ）。"""
        return self.card_ids[0]


@register
@dataclass(frozen=True)
class DrawAction(Action):
    """山札から引く。"""

    type: ClassVar[str] = "draw"


@register
@dataclass(frozen=True)
class ChooseColorAction(Action):
    """ワイルド後の色選択（応答待ちの継続、spec §3.6）。"""

    color: Color = field(metadata={"coerce": coerce_color})
    type: ClassVar[str] = "choose_color"


@register
@dataclass(frozen=True)
class DeclareUnoAction(Action):
    """UNO 宣言（「UNO!」ボタン）。手番外でも押せる割り込み（house-rules §6）。"""

    type: ClassVar[str] = "declare_uno"


@register
@dataclass(frozen=True)
class ChallengeUnoAction(Action):
    """UNO 宣言忘れの指摘（「UNO言ってない!」ボタン）。手番外の割り込み（house-rules §6）。"""

    type: ClassVar[str] = "challenge_uno"


@register
@dataclass(frozen=True)
class PassAction(Action):
    """ドロー後に「引いた札を出さずにパスする」（house-rules §7）。受理は awaiting 依存。"""

    type: ClassVar[str] = "pass"


@register
@dataclass(frozen=True)
class ResetAction(Action):
    """盤面のリセット／新規対戦（spec §8）。"""

    type: ClassVar[str] = "reset"


@register
@dataclass(frozen=True)
class NewGameAction(Action):
    """選択したローカルルール構成で新規ゲームを開始する（house-rules 設定, #85）。

    ``enabled_rule_ids`` は有効化するルール id の列（standard は必須で常に含まれる）。
    構成の適用と盤面再初期化はサーバ（Session）が行う（サーバ権威）。
    """

    enabled_rule_ids: tuple[str, ...] = field(metadata={"coerce": coerce_rule_ids})
    type: ClassVar[str] = "new_game"


def parse(raw: str | bytes | bytearray | dict) -> Action:
    """JSON 文字列/バイト列または dict から Action を復元・検証する。

    未知の種別、フィールドの欠落/余剰、型不一致は :class:`ActionError` で弾く。
    入力 dict は破壊しない（防御的にコピーする）。
    """
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            raise ActionError(f"不正な JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ActionError("Action ペイロードはオブジェクトであること")
    data = dict(raw)
    type_name = data.pop("type", None)
    if not isinstance(type_name, str) or type_name not in _REGISTRY:
        raise ActionError(f"未知の Action 種別: {type_name!r}")
    return _REGISTRY[type_name]._from_payload(data)


__all__ = [
    "Action",
    "ActionError",
    "PlayAction",
    "DrawAction",
    "ChooseColorAction",
    "DeclareUnoAction",
    "ChallengeUnoAction",
    "PassAction",
    "ResetAction",
    "NewGameAction",
    "register",
    "parse",
    "coerce_card_ids",
    "coerce_rule_ids",
]
