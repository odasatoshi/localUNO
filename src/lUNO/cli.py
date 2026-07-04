"""luno CLI エントリポイント。

想定フロー（docs/spec.md §11）: 起動時にカード画像を差分生成し、サーバを起動する。
カード画像の差分生成（#12）は結線済み。サーバ起動（#16）は後続 issue で実装する。
"""

from __future__ import annotations

import argparse

from lUNO import __version__
from lUNO.cards_render.generator import default_output_dir, generate_cards


def build_parser() -> argparse.ArgumentParser:
    """luno のコマンドライン引数パーサを構築する。"""
    parser = argparse.ArgumentParser(
        prog="luno",
        description="ローカルルールの UNO（二人対戦・LAN内専用）",
    )
    parser.add_argument("--version", action="version", version=f"luno {__version__}")
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="カード画像を全再生成する（既定は未生成分のみの差分生成）",
    )
    parser.add_argument("--host", default="0.0.0.0", help="待受ホスト（未実装 / #16）")
    parser.add_argument("--port", type=int, default=8000, help="待受ポート（未実装 / #16）")
    return parser


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。カード画像を差分生成し、サーバ起動意図を表示する。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    # カード画像の差分生成（spec §7, §11）。有効ルールの CardType 一覧を走査する
    # （現状は generator 既定の標準カード。ルールセット結線は #11/#16 で差し替える）。
    generated = generate_cards(regenerate=args.regenerate)
    out_dir = default_output_dir()
    if generated:
        mode = "全再生成" if args.regenerate else "差分生成"
        print(f"カード画像を{mode}: {len(generated)} 枚 → {out_dir}")
    else:
        print(f"カード画像は最新です（差分なし） → {out_dir}")

    # TODO(#16): サーバ起動（args.host / args.port）
    print("luno: サーバ起動（#16）は後続 issue で実装します。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
