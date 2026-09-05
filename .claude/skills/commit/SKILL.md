---
name: commit
description: 変更点と会話をもとに .ai/TODO.md と必要な知見を更新し、対象の変更を Conventional Commits 形式の英語メッセージでコミットする。「コミットして」「/commit」「$commit」と依頼されたときに使う。
---

# commit — .ai の記録を更新してコミットする

作業ルールは [AGENTS.md](../../../AGENTS.md) に従う。
変更点と会話の確認 → TODO の更新 → 必要なら KNOWLEDGE の更新 → stage → commit の順に進める。

## 1. 変更点と依頼範囲を確認する

```powershell
git status --porcelain=v1 --untracked-files=all
git diff
git diff --cached
git log --oneline -5
git branch --show-current
```

- untracked ファイルは diff に出ないため、中身も読む。
- 今回の依頼に属する変更をまとめる。既存の無関係な変更・ステージ済み変更を巻き込まない。
- 指定された作業ブランチを使う。完成した文書整備を main にコミットするだけなら、ブランチ確認を改めて求めない。

## 2. .ai を更新する

- [TODO.md](../../../.ai/TODO.md) の更新は必須。今回終えた作業を具体的に記録し、確認できたものだけ `[x]` にする。
  既に記録済みなら内容を確認し、重複した履歴や無意味な差分を作らない。
- 現在の見出し・採番を維持する。進行中の完了項目を整理し、後続 Backlog は未着手のまま残す。
- [KNOWLEDGE.md](../../../.ai/KNOWLEDGE.md) は、再利用できる原因・対処・注意点が見つかった場合に更新する。
  例は録音形式、モデル常駐、言語のフォールバック、Pi 固有の制約。一般論や未実測の性能を記録として水増ししない。
- `/commit` 自体は PLAN / SPEC のゴールや設計を変更しない。ユーザーが同時に方針変更を依頼した場合は、先にその文書更新を済ませる。

## 3. 確認してステージする

変更に必要な確認を行う。文書・スキルだけならリンク、YAML frontmatter、記述と差分の範囲を確認し、アプリのビルドや実機試験は行ったことにしない。

```powershell
# パスは今回確認した対象に置き換える。git add -A で無関係な変更を取り込まない。
git add -- AGENTS.md CLAUDE.md .ai .agents/skills .claude/skills
git diff --cached --check
git diff --cached --stat
git diff --cached
```

ステージ後にモデル、録音、議事録の実データ、ログ、秘密情報、生成物が混ざっていないか確認する。
対象外のファイルは今回自分がステージしたものだけ外し、既存のステージ状態を勝手に変更しない。

## 4. メッセージを作成してコミットする

- subject は `<type>: <動詞の原形> <具体的な対象>`。英語・小文字始まり・末尾のピリオドなし、50字程度を目安にする。
- scope は付けない。`feat` / `fix` / `refactor` / `docs` / `chore` / `test` / `perf` / `build` を内容で選ぶ。
- 文書のみなら `docs`、スキルや作業設定の追加なら `chore`。例: `chore: add project workflow skills`。
- body は必要なら日本語で理由を1〜3行書く。共同著者は実際の参加者が確認できる場合だけ記載し、参考元の名前・モデル名をコピーしない。
- 通常は関連する変更と `.ai/` の更新を1コミットにまとめる。
- 複数行メッセージは UTF-8 の一時ファイルへ書き、`git commit --file <message-file>` で渡す。一時ファイルをコミット対象にしない。
- `--no-verify` や `--amend` を使わない。hook が失敗したら原因を確認し、依頼範囲内で対処する。

## 5. 結果を確認して報告する

```powershell
git log -1 --format='%h %s'
git status --short
```

ハッシュ・subject、主な記録更新と検証結果、残った変更があればその内容を報告する。
push はユーザーが依頼した場合だけ実行し、`/commit` 単独では行わない。

変更がなければコミット不要と報告し、空コミットを作らない。
ユーザーの変更との区別がつかない場合や秘密情報を除外すると依頼の意味が変わる場合は、具体的な対象を示して必要な点だけ確認する。
