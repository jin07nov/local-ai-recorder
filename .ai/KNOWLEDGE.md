# KNOWLEDGE

> 同じ問題を繰り返さないための記録。プロジェクト固有の事実と教訓を残す。
> 初期項目は 2026-09-05 のソース調査による注意点であり、不具合の再現・実機計測ではない。

## 記録の形式

新しい記録を上に追加する。日付、確認方法（コード調査 / 実機検証 / ユーザー報告）、
症状または確認した事実、原因、対処または今後の対応、教訓、根拠を記載する。
未解決の項目に「解決済み」と書かず、試した環境・モデル・版を残す。

## 2026-09-05 元の翻訳画面でドイツ語を使うための経路

**確認方法**: ユーザーの追加依頼、コード調査、固定版 `moonshine-voice==0.0.65` の公式 Windows wheel（PyPI の SHA256 と照合）、HTTP の自動テスト。

**事実**: 固定版の STT カタログは `ar/es/en/ja/ko/vi/uk/zh` でドイツ語はない。一方、ネイティブ TTS カタログには `de-de` と複数の German Piper 音声がある。
Windows 版のカタログ取得では返却文字列の解放時に `0xc0000374` が発生した。検証用の短命プロセス内で解放だけを省略してカタログを確認した。これは Pi の動作試験や音声合成の成功を意味せず、回避策をアプリには入れていない。

**対処**: 元の3000番の画面に German を追加し、ドイツ語の STT だけ whisper.cpp へ接続する。既存 Float32 PCM を一時 WAV に変換し、成功・失敗時に後始末する。Gemma 翻訳は既存のレーン言語を使い、TTS は `de` → `de-de` とする。
元の `start.sh` も `.local/meeting.env` を読むため、会議で選択した Whisper モデルが使われる。会議用サーバーの起動は不要。
Whisper CLI も英語専用モデルを与えると `-l de` を `en` に変えるため、JSON の `model.multilingual` を検査して非英語の結果を拒否する。

**確認結果**: 自動テスト41件・両 UI のビルド・起動スクリプトの Bash 構文、フロントエンドの de 送信・エラー本文保持が成功。
**未確認**: Pi でのドイツ語音声認識・Gemma 翻訳・読み上げの品質、初回ダウンロード、オフライン、実際のキー操作。認識器と TTS をテスト用に置き換えた API 検証とは区別する。

**根拠**: [server.py](../backend/server.py)、[test_translator.py](../tests/test_translator.py)、[Pi の手順](../docs/translator.md)、[固定配布版](https://pypi.org/project/moonshine-voice/0.0.65/)、[Whisper CLI](https://github.com/ggml-org/whisper.cpp/blob/b4938/examples/cli/cli.cpp)。

## 2026-09-05 録音中の文字起こしと実機評価の境界

**確認方法**: Windows / Python 3.14 の自動テスト33件、両 UI のビルド、導入スクリプトの Bash 構文確認。
実推論には既存の公式 whisper.cpp `b4938` / 1.9.3 Windows CLI、多言語 `ggml-base.bin`、公開 `samples/jfk.wav` を使用した。

**実装・結果**: 録音と STT を別ワーカーにし、fsync 済み音声を既定10秒ずつ読み出す。推論の遅延・失敗・一時停止で録音が止まらないこと、再開・再起動・停止後の末尾処理・実 HTTP の部分結果取得を自動テストした。
実 CLI でも公開音声11秒をテスト用録音プロセスへ流し、録音状態のまま10秒の部分結果を保存し、停止後に残り1秒を処理できた。音声入力は実マイクではない。
試験データは Git 対象外の `.local/live-smoke-meetings/5cba895b4d6841aea834d554316200b6`。モデル・音声も `.local/` 内だけに置く。

**設計上の注意**: 追記中の PCM はサイズ・更新日時が変わるため、それをキャッシュキーにすると毎回やり直しになる。
会議 ID と認識条件・固定した区間長をキーにし、保存済みフレーム数で区間を確認する。音声ファイルはアプリ管理下の追記専用とし、手動差し替えは扱わない。
未処理音声はディスクに保持し、文字起こしの全文書き込み・推論を録音用ロックから外す。モデルは毎区間ロードするため、10秒＋推論時間の遅延と CPU・媒体の競合は残る。

**未確認**: Pi で実時間に追いつくか、日本語や分割境界の品質、30秒・10分・60分でのライブ表示、ブラウザの実操作。単語ごとの即時字幕とは区別し、実機 E2E を案内する。

**根拠**: [meetings.py](../backend/meetings.py)、[test_meetings.py](../tests/test_meetings.py)、[導入・ライブ E2E 手順](../docs/meeting-recorder.md)。

## 2026-09-05 Pi の0秒録音と、失われていた録音エラー

**確認方法**: ユーザー提供の Pi 画面、コード調査、テスト用子プロセスによる再現。

**報告**: 開始ボタンで会議は作成されるが、録音は0秒で失敗する。USB Composite Device は `arecord -L` にあり、アプリの録音先は `default`。
`--check` が成功しても、これは `arecord`・モデル・画面の配置確認であり、実マイクを開く試験ではない。

**確認した不具合**: 録音コマンドの標準エラーを破棄していたため、デバイス占有や形式不一致などの理由が分からず、WAV 確定時の「音声を取得できませんでした」だけが残る。

**対処**: 標準エラーを音声とは別スレッドで読み、最大4 KiBの末尾を保存する。失敗時は終了コード・デバイス名とともに画面上部へ表示する。
大量のエラー出力でも停止しないこと、録音済み音声を保持することを含め自動テスト23件と UI ビルドが成功した。

**追加のユーザー報告で判明**: `default` に対して `ALSA lib pcm_asym.c ... capture slave is not defined`、`audio open error: Invalid argument` が出る。
ALSA の `default` が使う asym PCM に録音用 slave が定義されていないため、録音機器を開けない。マイク故障や未接続を示すエラーではない。
`.local/meeting.env` の録音先を、一覧にある `plughw:CARD=Device,DEV=0` へ変更して再起動する。単独の `export` は起動時の設定ファイル読み込みで上書きされる。
その後、ユーザーから「録音できるようになりました」と報告あり。この環境で最終設定値・音声・認識品質を実測したわけではない。
過去の会議には以前のエラーが残るため、変更後の起動ログと新しい録音で確認する。

**根拠**: [meetings.py](../backend/meetings.py)、[test_meetings.py](../tests/test_meetings.py)、[切り分け手順](../docs/meeting-recorder.md)。

## 2026-09-05 P1 の確認範囲と実機 E2E

**確認方法**: Windows / Python 3.14、自動テスト21件、Vite ビルド、Git Bash の構文検査。
加えて公式 whisper.cpp `b4938` / 1.9.3 の Windows x64 CLI、多言語 `ggml-base.bin`、公開 `samples/jfk.wav` を使用。

**結果**: テスト・ビルド・構文検査が成功。公開音声11秒を10秒と1秒に分割し、実際の CLI で2区間を認識・保存できた。
自動テストでは実 HTTP とテスト用音声プロセスを使い、録音開始・停止・再開・削除・容量不足・異常終了からの復旧を確認した。
Moonshine の実モデルはテスト用認識器に差し替え、既存 `/api/stt` の入力・応答形式を確認した。

**未確認**: Pi 用インストーラーの実行、ALSA / USB マイク、日本語と長時間の品質・処理時間・温度、実際の既存翻訳と TTS。
作業環境には利用可能なブラウザがなく、UI 実操作も Pi で確認する。手順は [会議レコーダー](../docs/meeting-recorder.md)。

**教訓**: Windows の短音声の成功を Pi の実用性と置き換えない。E2E が必要な段階に達したことをユーザーへ案内し、30秒 → 10分 → 60分で進める。
検証用モデル・音声・結果は `.local/` に置き、Git に含めない。

## 2026-09-05 中断直後の空音声・容量不足でもサーバーを起動できるようにする

**確認方法**: 復旧コードのレビューと自動テスト。

**原因**: 録音開始直後に落ちると PCM が空のことがあり、WAV への復旧が失敗する。媒体の空き不足でも同じ問題が起きる。
会議の復旧エラーをサーバー全体の起動失敗にすると、一覧・削除も操作できなくなる。

**対処**: 復旧失敗をその会議のエラーとして保持する。PCM があれば WAV 未作成でも再処理可能とし、空き容量を確保した後に再実行できる。
途中で切れた入力 WAV は取り込み前に拒否する。録音時は書き込み済み PCM を残し、ブラウザや推論の失敗に依存させない。

**根拠**: [meetings.py](../backend/meetings.py)、[stt.py](../backend/stt.py)、[test_meetings.py](../tests/test_meetings.py)。

## 2026-09-05 会議機能を独立起動し、端末のマイクを直接録音する

**確認方法**: P1 の設計と実装。

**採用理由**: ブラウザ分割送信ではタブの終了・接続切れ・マイク権限に録音が依存する。Pi のバックエンド録音にすると操作画面を閉じても保存を続けられる。
会議サーバーは標準 Python と `arecord` / whisper.cpp のみで動作し、Gemma・Moonshine を起動しない。
既存翻訳は3000、会議は3001。元の起動スクリプト・kiosk・systemd を変更せず、専用スクリプトから利用する。

**注意**: SSH 越しにPCのブラウザから操作しても録音対象は Pi のマイク。デバイス指定は `.local/meeting.env` を編集する。
録音全体に比例して増えるのは保存ファイルで、ブラウザの音声 RAM ではない。PCM と WAV の両方で約230 MB/時を見込む。
既存翻訳と同時にマイクを使用した場合の挙動・資源競合は実機で確認する。

**根拠**: [meetings.py](../backend/meetings.py)、[導入手順](../docs/meeting-recorder.md)。

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

**P1 の対応**: 会議用はバックエンドの逐次 PCM 保存を追加した。既存翻訳のブラウザ録音は維持する。STT の差し替えだけで長時間録音対応が完了したことにしない。

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
初期調査時点では変更しなかった。P1 では `.local/` 全体の除外と会議本文を出さないログ処理を追加した。既存翻訳のログ処理は維持している。

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
