"""luno CLI エントリポイント。

想定フロー（docs/spec.md §11）: 起動時にカード画像を差分生成し、サーバを起動する。
現状は土台の骨格のみで、画像生成（#12）とサーバ起動（#16）は後続 issue で実装する。
"""

from __future__ import annotations

import argparse

from lUNO import __version__


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
        help="カード画像を全再生成する（未実装 / #12）",
    )
    parser.add_argument("--host", default="0.0.0.0", help="待受ホスト（未実装 / #14）")
    parser.add_argument("--port", type=int, default=8000, help="待受ポート（未実装 / #14）")
    return parser


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。骨格段階では起動意図を表示するのみ。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    # TODO(#12): カード画像の差分生成（args.regenerate で全再生成）
    # TODO(#16): サーバ起動（args.host / args.port）
    print("luno: 骨格のみ。画像生成（#12）とサーバ起動（#16）は後続 issue で実装します。")
    if args.regenerate:
        print("(--regenerate 指定: 全再生成は未実装)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
