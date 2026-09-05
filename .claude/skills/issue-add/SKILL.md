---
name: issue-add
description: 依頼されたタスクを短い日本語の GitHub Issue にする。「issueを作って」「issueにして」「/issue-add」「$issue-add」と依頼されたときに使う。Project 登録は対象も指定された場合に行う。
---

# issue-add — 依頼された作業を Issue にする

作業ルールは [AGENTS.md](../../../AGENTS.md) に従う。
ユーザーの一言を Issue にし、詳細設計は着手時に `.ai/PLAN.md` / `.ai/TODO.md` へ展開する。
コマンドの移植・設定やローカル Backlog の存在だけを Issue 作成の依頼とみなさない。

## 対象と内容

- 現在の対象は `jin07nov/local-ai-recorder`。実行時に `git remote get-url origin` で照合し、ユーザー指定があればそちらを優先する。
- fork の上流へ誤作成しないよう、GitHub CLI の全 Issue 操作に確定した `--repo` を渡す。
- 参考元の `ai-escape-game` の Project は使用しない。このリポジトリには既定の Project・期間を設定していない。
- タイトルはユーザーの日本語を活かし、表記ゆれだけ整える。別々の依頼は別 Issue にする。
- 本文に書く補足はユーザーが示したことだけ。担当者・ラベル・締切・実装案を勝手に足さない。
- Issue の内容が未指定なら、何を Issue にするか確認する。

## 本文の形式

```text
依頼された内容を1行で

- ユーザーから示された補足（なければ箇条書きは省く）

---
着手したら内容を `.ai/PLAN.md` / `.ai/TODO.md` へ展開する。詳細はそちら。
```

## 手順

1. 対象 repo と既存 Issue を確認する。

   ```powershell
   git remote get-url origin
   gh issue list --repo jin07nov/local-ai-recorder --state open --limit 100
   ```

2. 同じ作業が登録済みなら URL を返す。似たものとの統合・変更が必要な場合は対象を確認する。
3. 正確な本文を UTF-8 の一時ファイルへ書き、確定したタイトル・repo とともに作成する。

   ```powershell
   # 変数はユーザーの依頼と確認済み対象から設定する。
   gh issue create --repo $issueRepo --title $issueTitle --body-file $issueBodyPath
   ```

4. Project 登録も依頼されている場合のみ、対象 owner / number と実際のフィールドを読み取って登録する。
   CLI の `gh project --help` と対象サブコマンドの help で構文を確認し、参考元の Project ID・フィールド値・今日から7日という期間を流用しない。
5. 作成した Issue の番号・タイトル・URL、実施した Project 更新を報告する。

本文の追記依頼では既存本文を読んで補足を統合し、`gh issue edit <number> --repo <repo> --body-file <file>` で更新する。
作成コマンドの応答が途切れたら、再作成前に一覧で登録済みか確認する。
Project 登録だけが失敗した場合は作成済み Issue の URL と未完了の操作を報告し、Issue を重複作成しない。
このコマンドは実装着手・コミット・push・Issue のクローズを行わない。
