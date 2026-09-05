# KNOWLEDGE

> 同じ問題を繰り返さないための記録。プロジェクト固有の事実と教訓を残す。
> 初期項目は 2026-09-05 のソース調査による注意点であり、不具合の再現・実機計測ではない。

## 記録の形式

新しい記録を上に追加する。日付、確認方法（コード調査 / 実機検証 / ユーザー報告）、
症状または確認した事実、原因、対処または今後の対応、教訓、根拠を記載する。
未解決の項目に「解決済み」と書かず、試した環境・モデル・版を残す。

## 2026-09-05 ドイツ語は UI の選択肢追加だけでは動作しない

**確認方法**: コード調査。

**事実・原因**: `AVAILABLE_LANGUAGES`、`SUPPORTED_STT_LANGS`、`TTS_LANG_MAP` に `de` はない。
`get_stt_recognizer` と `get_tts_engine` は未対応の言語を `en` に置き換える。
画面の一覧だけにドイツ語を追加すると、STT は英語モデルを選ぶ経路になる。

**今後の対応**: ドイツ語入力は新しい whisper.cpp 経路で処理し、原文を Gemma へ渡して日本語などに翻訳する。
画面・セッション・STT・翻訳先の言語を一致させる。新規のライブ翻訳はテキスト表示を対象とし、
既存 TTS を維持する。ドイツ語 TTS や Moonshine モデルの対応可否はこのコード調査だけでは判断しない。

**根拠**: [TranslatorApp.jsx](../frontend/src/TranslatorApp.jsx)、[server.py](../backend/server.py)。

## 2026-09-05 既存の翻訳フローは録音停止後の一括処理

**確認方法**: コード調査。

**事実・原因**: `processTranslation` は録音停止後の音声を STT に送り、返った全文を Gemma に渡して訳文を表示する。
録音中の確定区間を継続配信する会議用フローや、翻訳待ちを管理する独立キューはない。

**今後の対応**: 既存フローを維持したまま、新機能には「原文の確定・保存・表示」と「後段の翻訳」を分けて追加する。
翻訳を別プロセスにするだけで資源競合が解消するとはみなさず、Pi で翻訳オフ/オンと過負荷時を比較する。
P1 は長時間 STT、P2 は録音中の区間処理とライブ翻訳を対象とする。

**根拠**: [TranslatorApp.jsx](../frontend/src/TranslatorApp.jsx)、[useAudioRecorder.js](../frontend/src/hooks/useAudioRecorder.js)。

## 2026-09-05 現在の録音は停止するまでメモリに蓄積する

**確認方法**: コード調査。

**事実・原因**: `useAudioRecorder` は各コールバックの Float32 音声を配列に追加し、
停止時に全体を結合・リサンプリング・Base64 化する。会議音声を逐次ファイルへ保存する経路はない。
入力は AudioContext のサンプルレートで蓄積されるため、16 kHz だけを前提に RAM 使用量を見積もれない。

**今後の対応**: 会議用に逐次保存方式を選定する。STT の差し替えだけで長時間録音対応が完了したことにしない。

**根拠**: [useAudioRecorder.js](../frontend/src/hooks/useAudioRecorder.js)、[audioHelpers.js](../frontend/src/utils/audioHelpers.js)。

## 2026-09-05 現在の STT 入力は WAV ではなく生 Float32 PCM

**確認方法**: コード調査。

**事実・原因**: `/api/stt` は Base64 を復号して `np.frombuffer(..., dtype=np.float32)` で読み、
16 kHz として Moonshine に渡している。返すのはセグメントを連結した `text` のみ。

**今後の対応**: whisper.cpp に渡す形式を明示して変換し、WAV ヘッダー・サンプル型・レートを確認する。
会議のタイムスタンプは STT 結果から保持する。既存 API にない情報を後付けで推測しない。

**根拠**: [server.py](../backend/server.py) の `handle_stt`、[api.js](../frontend/src/utils/api.js)。

## 2026-09-05 moonshine-voice は TTS でも使っている

**確認方法**: コード調査。

**事実・原因**: STT は `Transcriber`、TTS は `TextToSpeech` を同じ `moonshine_voice` から読み込む。
STT / TTS のキャッシュは別々で、上限もそれぞれ `MAX_MODELS = 2`。Gemma は別プロセスで動く。

**今後の対応**: STT の追加を理由にパッケージを削除しない。
Pi のメモリ評価は whisper.cpp 単体に限定せず、既存モデル・LLM・ブラウザを含めて行う。

**根拠**: [server.py](../backend/server.py)、[requirements.txt](../backend/requirements.txt)、[start.sh](../start.sh)。

## 2026-09-05 現在の ignore 設定は会議のテキスト保存先を除外していない

**確認方法**: コード調査。

**事実・原因**: `.gitignore` は `*.wav`、`*.bin` などを除外する一方、
将来の `transcript.md` / `summary.md` / `metadata.json` をまとめて除外する保存ディレクトリの規則はない。
また、現在の STT は認識結果、TTS は入力の先頭部分をログへ出力する。

**今後の対応**: 保存先が決まった段階でディレクトリ全体を追跡対象外にし、会議本文を通常ログに残さない設計にする。
今回は調査・計画のみで `.gitignore` やログ処理は変更していない。

**根拠**: [.gitignore](../.gitignore)、[server.py](../backend/server.py)。

## 2026-09-05 上流説明・別実装の制約・手元の実装を混同しない

**確認方法**: ローカルコードと公開資料の照合。

**事実**: 手元の実装は `moonshine-voice` を使う。
ユーザー提示の Moonshine 解説は `transcribe.cpp` に移植されたモデルについての資料であり、
そこに書かれた制約をこの Python 実装や Moonshine 全体へそのまま適用できない。
また、既存 README は Gemma Translator の説明で、会議機能の完成を示していない。

**教訓**: 「会議には whisper.cpp を採用する方針」と「この実機・音声で優れていたという結果」を分ける。
性能や精度は条件を揃えて測る。Gemma のモデル名も既存設定値として記録し、要約品質は別途評価する。

**根拠**: [requirements.txt](../backend/requirements.txt)、[README.md](../README.md)、
[transcribe.cpp の Moonshine 資料](https://github.com/handy-computer/transcribe.cpp/blob/main/docs/models/moonshine.md)、
[Moonshine 公式](https://github.com/moonshine-ai/moonshine)。

## 管理文書の参考元

`C:\Users\ysuzuki\Documents\github\ai-escape-game` の `AGENTS.md` / `CLAUDE.md` と `.ai/` を参考にした。
PLAN = ゴール、SPEC = 設計、TODO = タスク、KNOWLEDGE = ハマりどころという分担を採用した。
初期整備では参照元固有のゲーム設計・履歴・ブランチ名・補助スキルは移植していない。
後続の依頼で `/commit`・`/close`・`/issue-add` を `.agents/skills/` と `.claude/skills/` へ移植した。
ゲーム固有の GitHub Project と固定の共同著者名は引き継がず、close は後続計画を保全する手順に合わせた。
両方の定義とテンプレートは同内容に保つ。
