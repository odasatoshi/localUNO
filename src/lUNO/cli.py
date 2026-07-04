"""luno CLI エントリポイント。

起動フロー（docs/spec.md §11）: カード画像を差分生成 → サーバ（uvicorn）を起動する。
``--regenerate`` で全再生成、``--host``/``--port`` で待受を指定（既定は LAN 内向け 0.0.0.0）。
"""

from __future__ import annotations

import argparse

from lUNO import __version__
from lUNO.cards_render.generator import default_output_dir, generate_cards
from lUNO.server.app import run as run_server


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
    parser.add_argument("--host", default="0.0.0.0", help="待受ホスト（既定: 0.0.0.0 / LAN 内）")
    parser.add_argument("--port", type=int, default=8000, help="待受ポート（既定: 8000）")
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="画像の差分生成のみ行い、サーバは起動しない",
    )
    return parser


def generate(regenerate: bool = False) -> int:
    """カード画像を差分生成する（spec §7, §11）。生成枚数を返す。

    有効ルールが要求する CardType 一覧（既定は標準カード 54 種）を走査し、未生成分だけ
    描く。``regenerate`` で全再生成。
    """
    generated = generate_cards(regenerate=regenerate)
    out_dir = default_output_dir()
    if generated:
        mode = "全再生成" if regenerate else "差分生成"
        print(f"カード画像を{mode}: {len(generated)} 枚 → {out_dir}")
    else:
        print(f"カード画像は最新です（差分なし） → {out_dir}")
    return len(generated)


def main(argv: list[str] | None = None) -> int:
    """エントリポイント: 画像を差分生成し、サーバを起動する（spec §11）。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    generate(regenerate=args.regenerate)

    if args.no_serve:
        return 0

    print(
        f"luno: サーバを起動します（このホストは http://localhost:{args.port}/ 、"
        f"LAN 内の相手は http://<このホストのLAN IP>:{args.port}/ ）"
    )
    run_server(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
