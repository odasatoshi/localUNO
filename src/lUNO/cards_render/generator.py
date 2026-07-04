"""CardType メタデータ駆動の PNG 差分生成（Pillow）。

spec.md §7, §12 準拠:

- **メタデータ駆動** — 54 枚ハードコードループにしない。渡された CardType 一覧（既定は
  有効ルールが要求する ``standard_card_types()``）を走査して描く。ルール追加＝カード追加が
  画像生成まで一気通貫になる（§7）。
- **差分生成** — 出力先に既に存在する画像は描き直さない。``regenerate=True`` のときだけ全再生成。
- **重複排除** — 見た目（``CardType.image_key`` = 色_記号 / ワイルドは記号のみ）が同じ札は
  1 枚に畳む。label/effect だけ違う CardType が増えても画像は増えない（§4.1, cards.py）。
- **同梱フォント** — ``assets/DejaVuSans-Bold.ttf`` を bundle し環境差を消す（§7）。

デザインは公式 UNO の見た目を模倣しない**オリジナル**。ダーク基調のカード面に、色ごとの
アクセントカラーで縁取りと大きなグリフを描く。ワイルド系は白アクセント＋4色ドットで表す。

命名規約（§12 で確定）:

- 出力先: ``src/lUNO/static/cards/``（パッケージ同梱。サーバが静的配信、生成物は git 追跡外）
- ファイル名: ``<image_key>.png``（例: ``red_5.png`` / ``blue_skip.png`` / ``wild.png``）
- 画像サイズ: 300×450 の角丸カード（角の外側は透過）
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lUNO.engine.cards import (
    DRAW2,
    DRAW4,
    REVERSE,
    SKIP,
    WILD,
    CardType,
    Color,
    standard_card_types,
)

# --- 命名規約・寸法（spec §12 で確定） ---------------------------------------

CARD_SIZE = (300, 450)
CORNER_RADIUS = 34

# 同梱フォント（環境差を消すため必ずこれを使う。§7）
BUNDLED_FONT = Path(__file__).resolve().parent / "assets" / "DejaVuSans-Bold.ttf"


def default_output_dir() -> Path:
    """既定の出力先 ``src/lUNO/static/cards/``（パッケージ同梱・静的配信対象）。"""
    return Path(__file__).resolve().parent.parent / "static" / "cards"


# 安全なファイル名に使える image_key の文字集合。ローカルルールが symbol を自由に
# 足せる前提なので、出力先外への書き込み（`../` 等）や OS 予約文字を弾く。
_SAFE_KEY = re.compile(r"^[a-z0-9_]+$")


def card_filename(card_type: CardType) -> str:
    """カード面のファイル名。``<image_key>.png``（見た目一意キー・§4.1）。

    ``image_key`` は最終的にパス結合されるため、想定外の文字（``/``・``..``・空白等）を
    含む symbol は ``ValueError`` で弾く（出力先外書き込みやパス衝突の防止）。
    """
    key = card_type.image_key
    if not _SAFE_KEY.match(key):
        raise ValueError(
            f"image_key に使えない文字が含まれます: {key!r}（許可: 小文字英数字と _）"
        )
    return f"{key}.png"


# --- オリジナル配色 -----------------------------------------------------------

_BASE_BG = (26, 29, 41, 255)  # ダーク基調のカード面
_ACCENT: dict[Color, tuple[int, int, int]] = {
    Color.RED: (231, 76, 60),
    Color.YELLOW: (241, 196, 15),
    Color.GREEN: (46, 204, 113),
    Color.BLUE: (52, 152, 219),
}
_WILD_ACCENT = (240, 240, 240)  # ワイルド系のアクセント（白）
_WILD_DOTS = [_ACCENT[c] for c in (Color.RED, Color.YELLOW, Color.GREEN, Color.BLUE)]


def _accent(card_type: CardType) -> tuple[int, int, int]:
    """カードのアクセント色。ワイルド系（color=None）は白。"""
    return _ACCENT[card_type.color] if card_type.color is not None else _WILD_ACCENT


# 中央・隅に描くグリフの種別。skip/reverse/wild は独自図形、draw2/draw4 は "+2"/"+4"、
# 数字はその数字。それ以外の未知 symbol（ローカルルール由来）は **symbol 文字列をそのまま**
# 描く（label ではなく symbol を使う。専用の図柄が要るなら generator 側に分岐を足す想定）。
def _glyph_spec(card_type: CardType) -> tuple[str, str | None]:
    sym = card_type.symbol
    if sym == SKIP:
        return ("skip", None)
    if sym == REVERSE:
        return ("reverse", None)
    if sym == WILD:
        return ("wild", None)
    if sym == DRAW4:
        return ("text", "+4")
    if sym == DRAW2:
        return ("text", "+2")
    return ("text", sym)  # 数字 0-9


# --- グリフ描画（中央・隅で同じロジック。隅は縮小して回転貼付） --------------


def _glyph_tile(
    kind: str, text: str | None, accent: tuple[int, int, int], size: int, font_path: Path
) -> Image.Image:
    """1 個のグリフを透過タイル（size×size）に描いて返す。"""
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    c, s = size / 2, size

    if kind == "text" and text is not None:
        font = _fit_font(font_path, text, int(s * 0.82), int(s * 0.78))
        _draw_centered_text(d, text, (c, c), font, accent)
    elif kind == "skip":
        w = max(6, int(s * 0.09))
        pad = int(s * 0.12)
        d.ellipse((pad, pad, s - pad, s - pad), outline=accent, width=w)
        # 左上→右下の禁止線
        off = int(s * 0.26)
        d.line((off, off, s - off, s - off), fill=accent, width=w)
    elif kind == "reverse":
        _draw_reverse_arrows(d, size, accent)
    elif kind == "wild":
        _draw_wild_quadrants(d, size)
    return tile


def _draw_reverse_arrows(d: ImageDraw.ImageDraw, size: int, accent: tuple[int, int, int]) -> None:
    """向きの異なる 2 本の矢印で reverse を表す（オリジナル図形）。"""
    s = size
    w = max(5, int(s * 0.075))
    # 上段: 右上向きの矢印
    _arrow(d, (s * 0.24, s * 0.42), (s * 0.74, s * 0.30), w, accent)
    # 下段: 左下向きの矢印
    _arrow(d, (s * 0.76, s * 0.58), (s * 0.26, s * 0.70), w, accent)


def _arrow(
    d: ImageDraw.ImageDraw,
    start: tuple[float, float],
    tip: tuple[float, float],
    width: int,
    fill: tuple[int, int, int],
) -> None:
    """start→tip の矢印（軸線＋三角の矢じり）を描く。"""
    (x0, y0), (x1, y1) = start, tip
    dx, dy = x1 - x0, y1 - y0
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    ux, uy = dx / length, dy / length  # 進行方向の単位ベクトル
    px, py = -uy, ux  # 垂直方向
    head = width * 2.4  # 矢じりの長さ
    half = width * 1.7  # 矢じりの半幅
    base_x, base_y = x1 - ux * head, y1 - uy * head
    d.line((x0, y0, base_x, base_y), fill=fill, width=width)
    d.polygon(
        [
            (x1, y1),
            (base_x + px * half, base_y + py * half),
            (base_x - px * half, base_y - py * half),
        ],
        fill=fill,
    )


def _draw_wild_quadrants(d: ImageDraw.ImageDraw, size: int) -> None:
    """4 色の角丸パネルを 2×2 に並べる（ワイルドのオリジナルモチーフ）。"""
    s = size
    pad = int(s * 0.12)
    gap = max(3, int(s * 0.04))
    mid = s / 2
    r = max(4, int(s * 0.06))
    boxes = [
        (pad, pad, mid - gap, mid - gap),
        (mid + gap, pad, s - pad, mid - gap),
        (pad, mid + gap, mid - gap, s - pad),
        (mid + gap, mid + gap, s - pad, s - pad),
    ]
    for box, color in zip(boxes, _WILD_DOTS, strict=True):
        d.rounded_rectangle(box, radius=r, fill=color)


@lru_cache(maxsize=256)
def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    """サイズ別にフォントをキャッシュして返す（グリフ毎の再読込を避ける）。"""
    return ImageFont.truetype(font_path, size)


def _fit_font(font_path: Path, text: str, max_h: int, max_w: int) -> ImageFont.FreeTypeFont:
    """max_h/max_w に収まる最大サイズの同梱フォントを返す。"""
    fp = str(font_path)
    size = max_h
    while size > 6:
        font = _load_font(fp, size)
        box = font.getbbox(text)
        if (box[2] - box[0]) <= max_w and (box[3] - box[1]) <= max_h:
            return font
        size -= 2
    return _load_font(fp, 6)


def _draw_centered_text(
    d: ImageDraw.ImageDraw,
    text: str,
    center: tuple[float, float],
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
) -> None:
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x = center[0] - w / 2 - box[0]
    y = center[1] - h / 2 - box[1]
    d.text((x, y), text, font=font, fill=fill)


# --- カード 1 枚の描画 --------------------------------------------------------


def render_card(card_type: CardType, *, font_path: Path | None = None) -> Image.Image:
    """CardType 1 つを角丸カード PNG（RGBA）に描く。"""
    font_path = BUNDLED_FONT if font_path is None else font_path
    accent = _accent(card_type)
    w, h = CARD_SIZE
    img = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # カード面（角の外側は透過）＋アクセント縁取り
    d.rounded_rectangle(
        (5, 5, w - 6, h - 6),
        radius=CORNER_RADIUS,
        fill=_BASE_BG,
        outline=accent,
        width=10,
    )
    # 内側のうっすらした枠
    d.rounded_rectangle(
        (22, 22, w - 23, h - 23),
        radius=CORNER_RADIUS - 12,
        outline=accent,
        width=3,
    )

    kind, text = _glyph_spec(card_type)

    # 中央グリフ
    center_tile = _glyph_tile(kind, text, accent, 210, font_path)
    img.alpha_composite(center_tile, ((w - 210) // 2, (h - 210) // 2))

    # 隅グリフ: 左上（そのまま）と右下（180度回転）
    corner = _glyph_tile(kind, text, accent, 74, font_path)
    img.alpha_composite(corner, (26, 26))
    img.alpha_composite(corner.rotate(180), (w - 26 - 74, h - 26 - 74))

    return img


# --- 差分生成のエントリ -------------------------------------------------------


def generate_cards(
    card_types: Iterable[CardType] | None = None,
    out_dir: Path | str | None = None,
    *,
    regenerate: bool = False,
    font_path: Path | str | None = None,
) -> list[Path]:
    """CardType 一覧を走査し、未生成の PNG だけを生成する（差分生成）。

    Args:
        card_types: 描くカード定義。省略時は ``standard_card_types()``（有効ルール要求）。
        out_dir: 出力先。省略時は ``default_output_dir()``。
        regenerate: True なら既存も含め全再生成する（``--regenerate`` 相当）。
        font_path: 使うフォント。省略時は同梱 TTF。

    Returns:
        今回実際に生成（新規描画）したファイルのパス一覧。既に在って再生成しなかった
        ものは含まない。同じ入力で 2 回目は差分ゼロ → 空リストになる。

    Note:
        不要になった画像（対象集合から消えた CardType の PNG）は削除しない。
        ``regenerate`` も「現集合を上書き」するだけで orphan は残る（出力先は生成物専用で
        git 追跡外のため実害は小さい）。掃除が要るなら出力先を消してから呼ぶ。
    """
    types = list(standard_card_types() if card_types is None else card_types)
    out = Path(out_dir) if out_dir is not None else default_output_dir()
    out.mkdir(parents=True, exist_ok=True)
    fpath = Path(font_path) if font_path is not None else BUNDLED_FONT

    # 見た目一意キーで重複排除（label/effect 違いを 1 画像に畳む）
    unique: dict[str, CardType] = {}
    for ct in types:
        unique.setdefault(ct.image_key, ct)

    generated: list[Path] = []
    for ct in unique.values():
        path = out / card_filename(ct)
        if path.exists() and not regenerate:
            continue
        render_card(ct, font_path=fpath).save(path)
        generated.append(path)
    return generated
