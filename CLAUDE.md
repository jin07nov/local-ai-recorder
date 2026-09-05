# CLAUDE.md - local-ai-recorder

このリポジトリの共通作業ルールは [AGENTS.md](AGENTS.md) を正本とする。
作業開始時に必ず読み、同ファイルから参照される `.ai/` の4ファイルも確認する。

- [.ai/PLAN.md](.ai/PLAN.md): 今回のゴール・範囲・完了条件。
- [.ai/SPEC.md](.ai/SPEC.md): 現状と設計方針。
- [.ai/TODO.md](.ai/TODO.md): タスクと進捗。
- [.ai/KNOWLEDGE.md](.ai/KNOWLEDGE.md): 調査結果とハマりどころ。

共通ルールの変更は `AGENTS.md` に反映する。
現在の作業範囲は PLAN に従い、将来の実装タスクへ自動的に着手しない。

作業用コマンドは [/commit](.claude/skills/commit/SKILL.md)、[/close](.claude/skills/close/SKILL.md)、
[/issue-add](.claude/skills/issue-add/SKILL.md)。対応する定義を読んで実行する。
