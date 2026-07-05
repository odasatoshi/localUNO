# local-UNO

ローカルルールの UNO。二人対戦専用・LAN内専用。ブラウザからアクセスして遊ぶ。

設計は [`docs/spec.md`](docs/spec.md)、ローカルルールの追加方法は [`docs/rule-authoring.md`](docs/rule-authoring.md)、UI デザインシステムは [`docs/design.md`](docs/design.md) を参照。

## セットアップ

```bash
git config core.hooksPath .githooks   # main への直接 push を禁止するフック（整備後）
uv sync --extra dev                    # 開発環境（uv.lock どおりに再現）
```

## よく使うコマンド

```bash
uv run ruff check .       # lint
uv run pytest -q          # テスト
uv run luno --help        # CLI
```

> Python は `.python-version`（3.11）で固定。現状は土台の骨格のみで、エンジン・サーバ・画像生成・フロントは後続 issue で実装する。
