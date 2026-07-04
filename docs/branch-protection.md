# main ブランチ保護の設定手順

CLAUDE.md の規約「main 直 push 禁止・PR 経由必須・CI（`ci.yml`）グリーン必須」を GitHub 側で強制するための設定。リポジトリ管理者が一度実行する。

## 方針

- **PR 経由必須・直 push 禁止**、**CI（`ci.yml` の `test` ジョブ）グリーン必須**を有効化する。
- **必須「レビュー承認」は有効にしない**。本プロジェクトのレビューは Code Reviewer / Reality Checker エージェントの所見を PR コメントとして記録する運用であり、GitHub の承認（Approve）は用いないため（CLAUDE.md 参照）。承認必須にするとマージが不能になる。
- ローカルの直 push 防止は `.githooks/pre-push`（issue #5）で補完する。

## gh CLI での設定例

`ci.yml` が PR で一度実行され、チェック名（`test`）が GitHub に認識された後に実行する。

```bash
gh api -X PUT repos/odasatoshi/localUNO/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=test' \
  -f 'enforce_admins=false' \
  -f 'required_pull_request_reviews=' \
  -f 'restrictions=' \
  -f 'allow_force_pushes=false' \
  -f 'allow_deletions=false'
```

- `required_status_checks.contexts=[test]`：`ci.yml` の `test` ジョブの成功を必須にする。
- `strict=true`：main に追随済み（up-to-date）でないとマージ不可。
- `required_pull_request_reviews=` を空にし、承認必須を無効化。
- `enforce_admins=false`：緊急時に管理者が対応できる余地を残す（運用で直 push しない前提）。

## 確認

- 保護状態: `gh api repos/odasatoshi/localUNO/branches/main/protection`
- CI が落ちる PR がマージ不可になること、直 push が拒否されることを確認する。
