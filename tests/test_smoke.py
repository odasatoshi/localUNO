"""土台のスモークテスト: パッケージが import でき、CLI が起動すること。"""

from __future__ import annotations

import pytest

import lUNO
from lUNO.cli import build_parser, main


def test_package_has_version() -> None:
    assert lUNO.__version__


def test_subpackages_importable() -> None:
    import lUNO.cards_render  # noqa: F401
    import lUNO.engine  # noqa: F401
    import lUNO.rules  # noqa: F401
    import lUNO.server  # noqa: F401


def test_cli_help_exits_zero() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0


def test_cli_main_returns_zero() -> None:
    assert main([]) == 0


def test_cli_main_regenerate_flag() -> None:
    assert main(["--regenerate"]) == 0
