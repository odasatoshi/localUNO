# main ブランチ保護の設定手順

CLAUDE.md の規約「main 直 push 禁止・PR 経由必須・CI（`ci.yml`）グリーン必須」を GitHub 側で強制するための設定。リポジトリ管理者が一度実行する。

## 方針

- **PR 経由必須・直 push 禁止**、**CI（`ci.yml` の `test` ジョブ）グリーン必須**を有効化する。
- **必須「レビュー承認」は有効にしない**。本プロジェクトのレビューは Code Reviewer / Reality Checker エージェントの所見を PR コメントとして記録する運用であり、GitHub の承認（Approve）は用いないため（CLAUDE.md 参照）。承認必須にするとマージが不能になる。
- ローカルの直 push 防止は `.githooks/pre-push`（issue #5）で補完する。

## gh CLI での設定例

`ci.yml` が PR で一度実行され、チェック名（`test`）が GitHub に認識された後に実行する。

Branch protection の PUT API は真偽値・`null`・ネストしたオブジェクトを要求するため、型崩れを避けて **JSON を `--input` で流す**のが堅牢（`gh -f` は値を常に文字列化するため不可）。

```bash
gh api -X PUT repos/odasatoshi/localUNO/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["test"] },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

- `required_status_checks.contexts=["test"]`：`ci.yml` の `test` ジョブの成功を必須にする。
- `strict=true`：main に追随済み（up-to-date）でないとマージ不可。
- `required_pull_request_reviews=null`：承認必須を無効化（エージェント運用のため）。
- `restrictions=null`：push 可能な人の制限は設けない。
- `enforce_admins=false`：緊急時に管理者が対応できる余地を残す（運用で直 push しない前提）。

> 補足: `required_status_checks.contexts` は現行 API では legacy 扱いで、後継は `checks`（`[{ "context": "test" }]` 形式）。現時点では `contexts` も有効に機能するが、将来 `contexts` が廃止された場合は `checks` へ移行する。

## 確認

- 保護状態: `gh api repos/odasatoshi/localUNO/branches/main/protection`
- CI が落ちる PR がマージ不可になること、直 push が拒否されることを確認する。
