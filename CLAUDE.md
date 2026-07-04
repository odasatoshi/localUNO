# local-UNO 開発ガイド

ローカルルールのUNO
二人対戦専用、LAN内専用

## 開発フロー（必須・例外なし）

すべての作業単位で、以下を必ず順守する。**main への直接コミットは禁止**（初回土台構築のみ例外、以降禁止）。

1. **Issue** — GitHub issue を作成または選択する。作業内容・完了条件をそこに書く。**着手前に assignee が空か確認し、空なら `gh issue edit <n> --add-assignee @me` で自分に割当てて「着手」コメントを残す**（並行作業との衝突防止。詳細は下記「並行作業」）。
2. **ブランチ** — `git fetch origin` してから最新 `origin/main` を基点に専用ブランチを切る。命名は `feat/<issue番号>-<slug>` / `fix/<issue番号>-<slug>` / `docs/<issue番号>-<slug>` 等。
3. **実装＋試験** — 変更の挙動を**担保するテストを必ず書く**。ローカルで `uv run ruff check .` と `uv run pytest -q` を通す。
4. **PR 前レビュー** — **Code Reviewer エージェント**でレビューを受け、指摘を修正する。
5. **PR 発出** — push し、本文に `Closes #<issue番号>` とテスト結果を記載して PR を出す。**PR 作成後に、PR 前レビューの所見と対応を `gh pr comment` で記録する**（前レビューは PR 作成前に走るため、記録は作成後にまとめて行う）。
6. **PR 後レビュー** — **別人格のレビュアー（Reality Checker）**が PR をレビューする。**その所見・最終判定・対応を `gh pr review --comment` で記録する**。必要なら修正。
7. **マージ** — CI グリーン かつ 両レビュー通過後のみ。squash merge し、ブランチを削除する。

- レビュアーは 2 役とも Claude エージェントが担う（PR 前=Code Reviewer / PR 後=Reality Checker）。
- **レビュー所見は必ず GitHub の PR に記録する**：レビュー用サブエージェントは**所見の報告のみ**（issue/PR/コメント等の外部操作は禁止）。オーケストレータ（メインエージェント）が記録する — 前レビューは `gh pr comment`、判定を伴う後レビューは `gh pr review --comment`（判定は本文に明記し、`--approve`/`--request-changes` の強制力は使わない）。
- main ブランチ保護：**直 push 禁止・PR 経由必須・CI（`ci.yml`）グリーン必須**。

## 並行作業（複数人前提）

このリポジトリは**複数人（複数ワークツリー）で並行作業する前提**で運用する。

- **issue/PR/ブランチ番号を決め打ちしない**。他者が作成したものが混在し得るので、都度 `gh issue list` / `gh pr list` で最新を確認する。
- **他のローカルディレクトリ/リポジトリは触らない**。各自は自分の作業ディレクトリ内だけで作業する（他者のワークツリーやローカル clone を変更しない）。
- **issue の着手管理（衝突防止）**：着手中の issue は assignee で把握する（`gh issue list --state open` で確認）。
  - 着手前に `gh issue view <n>` で assignee を確認。**担当者がいる issue には着手しない**。
  - 空なら `gh issue edit <n> --add-assignee @me` で自分に割当て、「着手」コメントを残す。
  - **割当て直後にもう一度 `gh issue view <n>` を確認**（同時割当ての競合対策）。自分以外も付いていれば、着手コメントが後の側が `--remove-assignee @me` で譲る。
  - 完了は PR マージで自動クローズ（assignee は残るが `--state open` で絞れば実害なし）。中断・放棄時は `gh issue edit <n> --remove-assignee @me` で解放する。
  - **stale 対策**：数日以上更新のない他者の着手 issue は、確認コメントのうえ `gh issue edit <n> --remove-assignee <user>` で解放してよい。
- **同期**：ブランチ作成前に `git fetch origin`。作業中は `git rebase origin/main` で追随する。**push 済みブランチを rebase した場合は `git push --force-with-lease`**（`--force` は使わない）で更新し、衝突を早期に解消する。

## プロジェクト規約

- 言語：Python（>=3.11）、`src/lUNO` レイアウト。
- 環境・依存管理：uv（`uv.lock` を追跡）。Lint/Format：ruff。テスト：pytest。
- 依存は機能ごとに該当 feature PR で `uv add` して追加する。
- 回答・issue・PR・コミットメッセージは日本語で。

## 初回セットアップ

環境・依存管理は **uv** に統一（`uv.lock` で再現性を担保）。

```bash
git config core.hooksPath .githooks   # main への直接 push を禁止するフックを有効化
uv sync --extra dev                   # 開発環境（uv.lock どおりに再現）
```

## よく使うコマンド

```bash
uv run ruff check .       # lint
uv run pytest -q          # テスト
uv run luno --help        # CLI
uv add <pkg>              # 実行時依存の追加（uv.lock も更新）
uv add --optional dev <pkg>   # dev 依存の追加（[optional-dependencies].dev に入る）
```

> Python は `.python-version`（3.11）で固定。CI は `uv sync --locked` で lock ドリフトを検知するため、
> 依存を変えたら必ず `uv.lock` の更新をコミットすること。
