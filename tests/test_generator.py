"""cards_render.generator の試験（spec §7 の要件を担保）。

- 標準カード一式が生成される
- 2 回目は差分生成で描き直さない（--regenerate で全再生成）
- CardType を 1 つ足すと画像が 1 枚増える（メタデータ駆動）
"""

from __future__ import annotations

import pytest
from PIL import Image

from lUNO.cards_render.generator import (
    BUNDLED_FONT,
    CARD_SIZE,
    card_filename,
    generate_cards,
    render_card,
)
from lUNO.engine.cards import CardType, Color, standard_card_types


def _unique_image_keys(types) -> set[str]:
    return {ct.image_key for ct in types}


def test_bundled_font_exists() -> None:
    """同梱 TTF が存在する（環境差を消すため必須・§7）。"""
    assert BUNDLED_FONT.is_file()


def test_standard_set_generated(tmp_path) -> None:
    """標準カード一式（見た目一意キーの数）が PNG として生成される。"""
    types = standard_card_types()
    generated = generate_cards(types, tmp_path)

    keys = _unique_image_keys(types)
    assert len(generated) == len(keys)
    for key in keys:
        f = tmp_path / f"{key}.png"
        assert f.is_file()

    # 主要な札の存在と命名規約
    for name in ("red_0.png", "blue_skip.png", "green_reverse.png", "wild.png", "draw4.png"):
        assert (tmp_path / name).is_file()


def test_generated_png_is_valid_and_sized(tmp_path) -> None:
    """生成物は開ける PNG で、規約どおりのサイズ・透過を持つ。"""
    generate_cards([CardType(symbol="7", color=Color.RED, label="7")], tmp_path)
    with Image.open(tmp_path / "red_7.png") as im:
        assert im.size == CARD_SIZE
        assert im.mode == "RGBA"


def test_second_run_is_diff_only(tmp_path) -> None:
    """2 回目は未生成分のみ = 差分ゼロで描き直さない。"""
    types = standard_card_types()
    first = generate_cards(types, tmp_path)
    assert first  # 初回は生成される

    second = generate_cards(types, tmp_path)
    assert second == []  # 差分なし


def test_regenerate_redraws_all(tmp_path) -> None:
    """--regenerate 相当で既存も含め全再生成する。"""
    types = standard_card_types()
    generate_cards(types, tmp_path)
    again = generate_cards(types, tmp_path, regenerate=True)
    assert len(again) == len(_unique_image_keys(types))


def test_adding_card_type_adds_one_image(tmp_path) -> None:
    """CardType を 1 つ足すと、差分生成で画像が 1 枚だけ増える（メタデータ駆動）。"""
    base = standard_card_types()
    generate_cards(base, tmp_path)
    before = len(list(tmp_path.glob("*.png")))

    # 標準には無い見た目の新カード（ローカルルール想定）
    extra = CardType(symbol="swap", color=Color.RED, label="Swap")
    assert extra.image_key not in _unique_image_keys(base)

    added = generate_cards([*base, extra], tmp_path)
    assert added == [tmp_path / card_filename(extra)]
    assert len(list(tmp_path.glob("*.png"))) == before + 1


def test_dedup_by_image_key(tmp_path) -> None:
    """label/effect だけ違う同一見た目の CardType は 1 画像に畳む（§4.1）。"""
    a = CardType(symbol="skip", color=Color.BLUE, label="Skip")
    b = CardType(symbol="skip", color=Color.BLUE, label="スキップ", effect="skip_next")
    assert a.image_key == b.image_key
    generated = generate_cards([a, b], tmp_path)
    assert len(generated) == 1


def test_render_card_returns_image() -> None:
    """render_card は指定サイズの RGBA 画像を返す（保存前の単体確認）。"""
    im = render_card(CardType(symbol="wild", color=None, label="Wild"))
    assert im.size == CARD_SIZE
    assert im.mode == "RGBA"


def test_color_and_symbol_affect_rendering(tmp_path) -> None:
    """色・記号の違いが実際に別画像になる（分岐の退行検知）。"""
    generate_cards(
        [
            CardType(symbol="5", color=Color.RED, label="5"),
            CardType(symbol="5", color=Color.BLUE, label="5"),
            CardType(symbol="skip", color=Color.RED, label="Skip"),
        ],
        tmp_path,
    )
    red5 = (tmp_path / "red_5.png").read_bytes()
    blue5 = (tmp_path / "blue_5.png").read_bytes()
    red_skip = (tmp_path / "red_skip.png").read_bytes()
    assert red5 != blue5  # 色違い
    assert red5 != red_skip  # 記号違い


@pytest.mark.parametrize("bad", ["../evil", "red/5", "with space", "UPPER"])
def test_unsafe_image_key_rejected(tmp_path, bad) -> None:
    """パス外書き込みや予約文字を招く symbol は ValueError で弾く。"""
    with pytest.raises(ValueError):
        generate_cards([CardType(symbol=bad, color=None)], tmp_path)


def test_cli_generates_cards(tmp_path, monkeypatch) -> None:
    """`luno` 起動で差分生成が走る（--regenerate 結線の担保）。"""
    from lUNO import cli

    def fake_generate(regenerate=False):
        return generate_cards(out_dir=tmp_path, regenerate=regenerate)

    monkeypatch.setattr(cli, "default_output_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "generate_cards", fake_generate)
    assert cli.main([]) == 0
    assert list(tmp_path.glob("*.png"))  # 何か生成された
    # 2 回目は差分ゼロでも正常終了
    assert cli.main([]) == 0
    # --regenerate も正常終了
    assert cli.main(["--regenerate"]) == 0
