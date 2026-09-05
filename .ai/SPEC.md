# SPEC

> 現状と、ゴールに向けた設計の記録。**採用方針・設計案は実装済みを意味しない。**
> バージョンの正本は依存定義・ロックファイル・導入スクリプト。初期調査は 2026-09-05、HEAD `b194f30`。P1 の実装を同日追記。

## 現在の実装

| 領域 | 確認した内容 | 根拠 |
|---|---|---|
| UI | React 18 / Vite 5。小型画面向けの2人用翻訳 UI | [package.json](../frontend/package.json)、[TranslatorApp.jsx](../frontend/src/TranslatorApp.jsx) |
| 録音 | Web Audio で Float32 PCM をメモリに蓄積。停止時に結合・16 kHz モノラル化・Base64 化 | [useAudioRecorder.js](../frontend/src/hooks/useAudioRecorder.js) |
| STT | `POST /api/stt` が `audio_base64` / `language` を受け取り、既存言語は Moonshine、ドイツ語は whisper.cpp の結果を `{"text": ...}` で返す | [api.js](../frontend/src/utils/api.js)、[server.py](../backend/server.py) |
| API | `http.server` / `ThreadingTCPServer`。ポート 3000。推論は STT / TTS それぞれのロックで保護 | [server.py](../backend/server.py) |
| 翻訳 | ローカル LiteRT-LM のポート 9379 を利用。モデル名の既定は `gemma4-e2b` | [api.js](../frontend/src/utils/api.js)、[download_model.sh](../download_model.sh) |
| TTS | `moonshine-voice` の `TextToSpeech`。STT と同じ Python パッケージを利用 | [server.py](../backend/server.py)、[requirements.txt](../backend/requirements.txt) |
| 言語 | 翻訳 UI は既存6言語と `de`。ドイツ語は whisper.cpp STT、Gemma 翻訳、moonshine-voice `de-de` TTS。会議 UI へのドイツ語追加は後続 | [TranslatorApp.jsx](../frontend/src/TranslatorApp.jsx)、[server.py](../backend/server.py) |
| 起動・配備 | 開発 UI は 5173。本番は Python が `frontend/dist/` を配信。systemd / Chromium kiosk の配備スクリプトあり | [start.sh](../start.sh)、[deploy-pi.sh](../deploy-pi.sh) |
| 会議録音 | Pi の `arecord` → 逐次 PCM 保存 → 停止後 WAV 確定。会議一覧・中断復旧・削除 | [meetings.py](../backend/meetings.py) |
| 会議 STT | whisper.cpp CLI を別プロセスで呼び、録音中の短区間処理または停止後の処理で原文・時刻を保存。一時停止・再開に対応 | [stt.py](../backend/stt.py)、[meetings.py](../backend/meetings.py) |
| 会議 UI / API | ポート3001の独立サーバー。標準 Python のみで起動。会議 UI は Vite の別エントリとしてビルド | [meeting_server.py](../backend/meeting_server.py)、[MeetingApp.jsx](../frontend/src/meeting/MeetingApp.jsx) |
| 会議導入 | whisper.cpp `b4938` のソースビルド、多言語 `base` / `small`、UI の構築。既存サービス設定は維持 | [setup-meeting.sh](../setup-meeting.sh)、[start-meeting.sh](../start-meeting.sh) |
| 後続機能 | 会議のドイツ語選択・ライブ翻訳・要約・TODO 生成は未実装 | P2 / P3 の計画 |

既存起動手順は [README.md](../README.md) を参照する。記載された導入手順は主に Linux / macOS 向けで、
今回の Windows 作業環境で Pi の動作を検証したわけではない。

## 採用方針

1. オリジナルの機能・操作を維持する。Translator Mode の Moonshine → Gemma → TTS、既存言語、2人用 UI、キー操作、設定、起動方法を回帰確認する。
2. 直近のゴールは whisper.cpp による長時間文字起こし。STT の共通境界への分離は追加に必要な範囲で行い、既存機能の全面改修を前提にしない。
3. 会議の音声を先に保存し、文字起こしと議事録を再生成可能な派生データとして扱う。
4. 会議の録音中に保存済み音声を順次処理する。録音停止後の処理も選べる。長時間の録音全体をブラウザ RAM に保持しない。
5. 設定・モデル準備後の処理は端末内で完結させる。会議要約に外部 API を必須にしない。
6. 次段階でドイツ語の選択肢とライブ翻訳・フロントエンド表示を追加する。翻訳のために録音・文字起こしを止めない。
7. Pi 5 / 8GB で段階ごとに評価する。P1 で長時間 STT、P2 でドイツ語 → 日本語の連続処理を検証し、モデルの最終決定は実測に基づく。

これは今回の用途に対する設計判断であり、「Moonshine は長時間音声を処理できない」という断定ではない。
Moonshine の公式説明は低遅延の音声インターフェースを重視している。
whisper.cpp はファイル入力・タイムスタンプ・VAD を提供しており、会議の処理基盤として検証する。
出典: [Moonshine](https://github.com/moonshine-ai/moonshine)、[whisper.cpp](https://github.com/ggml-org/whisper.cpp)。

## 処理構成（Translator / P1 は実装、P2 / P3 は計画）

```text
Translator Mode
  短い発話 → MoonshineSTT（既存6言語）/ WhisperCppSTT（ドイツ語）
    → Gemma（翻訳）→ TTS（ドイツ語は de-de）

Meeting Mode / P1（直近のゴール）
  録音開始 → 音声の逐次保存 → 録音停止
    → WhisperCppSTT → タイムスタンプ付き原文の保存・表示

Meeting Mode / 録音中の文字起こし（追加実装）
  録音 → PCM の逐次保存（推論を待たずに続行）
    → 既定10秒の保存済み音声 → WhisperCppSTT → 原文の保存・逐次表示
  録音停止後 → 残りの短区間・未処理分を続行

Meeting Mode / P2（次段階。例: ドイツ語 → 日本語）
  録音・音声の逐次保存 → 確定した音声区間 → WhisperCppSTT（ドイツ語）
    → 原文区間の確定・保存・即時表示
      → 翻訳待ちキュー → Gemma（日本語訳）→ 訳文の順次表示
  ※ 録音・STT は翻訳の完了を待たずに続行する

P3（後続）
  保存済み文字起こし → Gemma（分割要約 → 全体統合）→ 議事録・TODO
```

### STT の境界（P1 実装）

- `SpeechToText.transcribe(audio_path, language)` の入力は 16 kHz / mono / 16-bit PCM WAV。形式・空音声・データ途切れを検査する。
- `Transcription` は `text` / `language` / `engine` / `segments`。各区間は `start` / `end`（秒）/ `text`。モデル情報は会議メタデータ・処理識別情報へ保存する。
- `MoonshineSTT.transcribe_samples` は既存の Float32 PCM をそのまま既存キャッシュ・ロックへ渡す。既存 `POST /api/stt` の入力と `{"text": ...}` 応答は維持し、時刻は生成しない。
- Translator Mode は既存6言語を Moonshine、追加のドイツ語だけ whisper.cpp とする。会議は whisper.cpp。全体を切り替える `STT_ENGINE` は導入しない。
- `WhisperCppSTT` は `whisper-cli -m ... -f ... -l ... -t ... -oj -of ... -np -ng` をシェルを介さず起動する。翻訳オプション `-tr` は指定せず原言語を認識する。
- `b4938` の JSON `transcription[].offsets.from/to` はミリ秒として読み、秒へ変換する。終了コード・モデル欠落・結果欠落・時刻不正・タイムアウト・キャンセルを扱う。
- 区間推論のタイムアウトとキャンセル時は子プロセスを終了する。モデル出力は通常ログへ出さず、保存 JSON を読み込む。
- 設定の正本は `.local/meeting.env`。`start-meeting.sh` が読み込み、Python は環境変数を使用する。`--host` / `--port` の明示引数は環境変数より優先する。
  会議用の設定名・初期値は [導入手順](../docs/meeting-recorder.md) を参照する。

### 長時間録音・処理ジョブ（P1 実装）

- 操作ブラウザの接続やマイク許可に依存しないよう、Pi のバックエンド録音を採用した。`arecord` の raw S16_LE / 16 kHz / mono を毎秒程度ファイルへ flush / fsync する。ブラウザ RAM に全音声を蓄積しない。
- 停止時に PCM から WAV をストリーム書き込みする。復旧用 PCM は保持し、起動時には中断状態を復旧する。空音声・容量不足は会議ごとのエラーにし、他の会議の操作を妨げない。
- 録音時間の初期上限は7200秒、設定可能範囲は1〜21600秒。空き容量の下限256 MiBに加えて、WAV 確定分を予約する。媒体故障や外部プロセスが容量を消費した場合まで保証しない。
- 録音停止後のみ処理するモードと WAV 取り込みでは、WAV を既定300秒の連続区間に分割する。音声サンプルを重複・欠落させず、STT の区間時刻を全体時刻に戻す。音声上の境界発話の認識品質は実機評価待ち。オーバーラップ・重複テキストの統合は未導入。
- VAD は既定オフ。`WHISPER_VAD_MODEL` を指定した場合だけ CLI の `--vad -vm` を使う。VAD の有無によらず原音の無音は保存する。
- 区間結果を原子的に保存して再開する。停止後のみの処理では音声のサイズ・更新時刻、モデルと CLI のパス・サイズ・更新時刻、言語・区間長・スレッド数などが変わると別の処理として再生成する。録音中のキャッシュ条件は下記を参照する。
- 1サーバーで1会議を処理し、その会議の録音と STT は同時に動かせる。他会議の録音・処理は排他。Gemma・Moonshine は会議サーバーからロードしない。保存先の OS ファイルロックで二重起動を防ぐ。
- 状態は `recording` / `stopping` / `recorded` / `transcribing` / `completed` / `interrupted` / `failed`。処理はワーカースレッドで進め、HTTP はすぐに応答する。
- UI は1秒間隔で状態・区間結果をポーリングし、接続断や画面再読込後も一覧から復帰する。P2 では独立した翻訳状態を追加する。
- `arecord` の標準エラーは専用スレッドで読み続け、末尾4 KiBだけ保持する。録音が失敗した場合はデバイス名・終了コードとともにメタデータと画面上部へ表示する。エラー出力が大量でも音声パイプを詰まらせない。
- `--check` の録音利用可否は `arecord` の存在確認で、実マイクの試験ではない。実際のデバイス指定と短時間録音による切り分けは導入手順を参照する。

### 録音中の文字起こし（追加実装）

- 開始時の `live` が true の会議に適用する。UI のチェックボックスは既定オン。API は互換性のため省略時 false とし、録音のみ・停止後処理も維持する。
- `MEETING_LIVE_CHUNK_SECONDS` は既定10秒、3〜60秒。録音開始時に会議へ保存し、設定変更は新規会議に適用する。既存設定に行がなくても10秒で動く。
- 録音ワーカーは PCM を fsync してから保存済み時間を公開し、別 STT ワーカーがその範囲だけを読む。音声の待ち行列は保存ファイルと処理位置で管理し、RAM に全音声を積まない。
- 推論は録音・メタデータのロック外で実行し、原文ファイルの書き込みも録音のロック外で行う。CPU・メモリ・媒体帯域の競合は Pi で評価する。
- 連続した短区間を1つずつ既存 CLI で処理するため、各区間でモデルをロードする。表示遅延は区間長＋推論時間＋画面更新待ち。語中の境界や短い末尾の認識精度は未保証。単語単位の未確定字幕は実装しない。
- `capture_status` は `recording` / `stopped` / `failed`。録音中の STT は `transcription_status` の `waiting` / `processing` / `paused` / `failed` / `completed` で独立管理し、STT エラーは `transcription_error` に保存する。
- STT を停止・失敗しても録音の全体状態は `recording` / `stopping` を維持する。`cancel` は STT だけを止め、`transcribe` で録音中にも再開できる。
- 録音停止後、動作中の STT は未処理分と最後の短区間まで続行する。STT を一時停止・失敗していた場合は自動再開せず、画面から再開する。音声保存の異常は原文の処理成功で消さない。
- `transcribed_seconds` は処理済み音声の終端。詳細 API の `transcription_lag_seconds` は保存済み音声との差で、現在の区間を待つ時間も含む。UI に時間・状態を表示し、遅延が2区間以上になると未処理時間を案内する。
- 区間結果に音声フレーム数と全体時刻を保存し、原文全体へ重複しない ID を付ける。キャッシュ識別子は会議 ID・認識器の条件・言語・その会議の区間長を使う。追記中の PCM のサイズ／更新日時は使わない。管理下の音声は追記専用で、手動差し替えは対象外とする。
- 同じ条件での再開は保存済み区間を再利用する。モデル等の条件変更は別キャッシュとし、原音から処理する。サーバー再起動後は録音音声を復旧し、文字起こしは一時停止状態から手動再開できる。
- 原文欄の末尾を見ている場合は新しい区間へ追従し、上へスクロールした場合は追従を止める。録音中でも部分的な原文をダウンロードできる。

### 会議 API と起動（実装）

| 操作 | API |
|---|---|
| 利用準備・一覧 | `GET /api/meetings/status`、`GET /api/meetings` |
| 録音開始 | `POST /api/meetings`、JSON `title` / `language` / 任意の `live`（boolean） |
| 会議詳細・原文区間 | `GET /api/meetings/<id>` |
| 停止・文字起こし・中断 | `POST /api/meetings/<id>/stop` / `transcribe` / `cancel` |
| ダウンロード | `GET /api/meetings/<id>/audio.wav` / `transcript.md` / `transcript.json` |
| 削除 | `POST /api/meetings/<id>/delete`。UI で確認し、処理中の対象は拒否 |

変更操作は JSON、本文16 KiB以下、ブラウザでは同一 Origin に限定する。既定はループバック接続。
Pi の `http://localhost:3001` から使用する。別PCはSSHポート転送で接続する。
既存翻訳は3000、Gemmaは9379のまま。元の `start.sh` / `deploy-pi.sh` とサービス設定は変更しない。
UI 開発時は Vite の `/meeting.html` を使い、`/api/meetings` だけ3001へプロキシする。

### 元の Translator Mode のドイツ語（追加実装）

- 両レーンの言語一覧に German / `de` を追加。既存の Space / Z / X / 言語切り替え・TTS 設定と、録音停止後の翻訳フローを維持する。
- `POST /api/stt` のドイツ語入力は16 kHzの生 Float32 PCMを一時的なmono PCM16 WAVに変換し、`WhisperCppSTT` に `de` を指定する。NaN等の不正な音声は拒否する。
- 同じ STT ロックで直列化し、既存6言語は従来の Moonshine キャッシュへ渡す。`get_stt_recognizer("de")` は拒否し、英語へフォールバックしない。
- Whisper の実モデル情報が英語専用を示す場合は非英語入力の結果を拒否する。モデルを別名にしても英語への暗黙切り替えを成功と扱わない。
- 元の `start.sh` が `.local/meeting.env`（または `MEETING_ENV_FILE`）を読み、ドイツ語認識に既存の Whisper 設定を使う。会議サーバー3001を起動しておく必要はない。
- 一時音声は Git / 静的配信の対象外の `.local/translator-tmp/stt-*/audio.wav` に置く。通常の成功・失敗時に削除する。強制終了時の残存はあり得る。翻訳画面の録音を会議として保存する機能は追加しない。
- 訳文の言語は既存の相手レーンから Gemma のプロンプトへ渡す。ドイツ語の TTS は `de` → `de-de` とし、固定依存 moonshine-voice 0.0.65 の TextToSpeech を利用する。言語データ・音声は初回に取得し、以後キャッシュを利用する。
- モデル未配置などの STT エラーは本文を画面に表示する。Pi の音声認識・Gemma 翻訳品質・読み上げ・画面実操作は検証待ち。
- 手順は [元の翻訳画面・ドイツ語](../docs/translator.md)。会議のライブ翻訳やSTT優先の資源制御を実装済みとはしない。

### 会議のドイツ語入力・ライブ翻訳・表示（P2 / 未実装）

- 入力言語の選択肢にドイツ語（`de` / Deutsch / German）を追加し、最初の受け入れ例をドイツ語 → 日本語（`ja`）とする。
- ドイツ語入力は新しい whisper.cpp の経路で原言語の文字起こしを行い、確定テキストを Gemma で日本語に翻訳する設計とする。品質は実機で評価する。
- 言語一覧だけを追加せず、選択値を録音セッション・STT・翻訳先・表示まで引き渡す。`de` を既存 Moonshine の未対応言語フォールバックに流さない。
- 既存 UI と言語一覧を共有する場合も、モード・STT・翻訳・TTS の対応能力を区別する。
  固定版 Moonshine STT のカタログにドイツ語はない。TTS は `de-de` があり、Translator Mode へ接続した。実機品質は検証待ち。
- 新しい会議のライブ翻訳の必須出力は画面のテキスト。Translator Mode の TTS は維持し、会議での訳文の読み上げは対象外とする。
- 先行実装した録音中の文字起こしを利用し、原文区間が確定してから翻訳する。単語ごとの未確定字幕を必須にしない。
- 原文区間の ID・開始/終了時刻・原言語を保存し、訳文は同じ区間 ID と翻訳先言語で対応付ける。
  原文を先に表示し、訳文が到着した区間へ追記する。到着順が変わっても会議順を維持し、原文を訳文で上書きしない。
- フロントエンドには原文と訳文、翻訳待ち・翻訳遅延・一時停止・失敗を区別して表示する。
  ライブ翻訳のオン/オフは録音・STT から独立させる。途中の言語変更の適用範囲は実装時に定義する。
- 更新の配信方式（SSE / WebSocket / ポーリング）は未決定。区間 ID による重複排除と、再接続時の取得漏れ回収を設計する。

### 文字起こしを妨げない条件（P2 の必須要件）

- 録音・保存・STT は翻訳ジョブの終了や空き待ちに依存させない。原文確定後に翻訳対象の区間 ID を登録する。
- 翻訳の同時実行数とメモリ上の待ち件数に上限を設ける。遅延分は保存済み原文と処理位置から後で再開し、RAM に無制限に積まない。
- キューやプロセスを分けるだけでは CPU・メモリの競合は解消しない。STT 用の資源を確保し、負荷時は翻訳を遅延・一時停止する方式を評価する。
- タイムアウト・Gemma 停止・翻訳エラー時も音声と原文を保存・表示し続ける。翻訳だけを再試行できるようにする。
- 同一音声・同一モデルで翻訳オフ/オンを比較し、録音欠落、STT の欠落・重複・遅延・滞留、最大メモリを測る。
  許容する STT 遅延の増加とライブ翻訳の更新間隔は P2 の実測で定める。目標を超える場合は STT を優先する。
- 「ライブ」は会議中に訳文が順次現れることを指す。翻訳停止時の耐障害性に加え、通常時の継続表示も受け入れ条件にする。

### 保存構成（P1 実装）

`MEETING_DATA_DIR` の既定はリポジトリ内 `.local/meetings`。会議 ID は UUID の32桁 hex で、日時は時差付きでメタデータへ保存する。

```text
<MEETING_DATA_DIR>/
  .lock
  <uuid>/
    audio.pcm          # マイクから逐次保存する復旧用音声
    audio.wav          # 停止後の WAV（CLI 取り込み時も同形式）
    transcript.md      # タイムスタンプ付きの原文
    transcript.json    # 原文区間 ID・秒単位の時刻・処理識別情報
    metadata.json      # ID・開始日時/時差・言語・モデル・状態・進捗
    chunks/<処理条件の識別子>/  # 完了区間の JSON、処理中の一時 WAV
```

- 音声は 16 kHz / mono / 16-bit PCM。マイク録音では復旧用 PCM と WAV の両方を保持し、計約230 MB/時。CLI 取り込みでは WAV のみを保持する。
- 原文・メタデータは一時ファイルへの書き込み・fsync 後に置換する。確定済み区間は推論失敗・中断後も残す。会議の削除は音声・区間結果を含む全体を対象とする。
- 保存先は静的 UI 配信ディレクトリの外。初期保存先・設定・モデルを含む `.local/` 全体を Git から除外した。`MEETING_DATA_DIR` を変更する場合も実データを追跡対象にしない。
- `translation.ja.md`（P2）、`summary.md`（P3）は後続の案であり、まだ作成しない。

### 要約・議事録（P3 / 後続）

- Gemma / LiteRT-LM の既存基盤を活用する。翻訳用プロンプトと要約用プロンプトは分ける。
- 長い文字起こしは入力長の予算に沿って分割し、区間ごとの要約を全体へ統合する。
- `summary.md` は「3行要約」「決定事項」「TODO（内容・担当者・期限）」「未決事項」を基本にする。
- 担当者・期限が発言にない場合は「未指定」、決定か不明なものは「要確認」とする。
  該当事項がなければ無理に埋めない。確認に戻れるよう根拠の区間・時刻を紐付ける。
- 話者分離・話者の実名特定は初期 MVP の対象外。TODO の担当者抽出と話者認識を混同しない。

## モデル評価の計画（未実施）

多言語版の `ggml-base.bin` から始め、`ggml-small.bin` と比較する。
`.en` モデルは英語専用なので、この比較の対象にしない。
モデル種別の根拠: [whisper.cpp models](https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md)。

- P1 は日本語の基準音声で短い発話 → 10分 → 60分の順に確認する。P2 に向けてドイツ語の基準音声も用意し、原文の認識品質と日本語訳の品質を別々に評価する。
- 処理時間、音声時間に対する処理時間の比、最大メモリ、温度、保存容量を記録する。
- 固有名詞・数字・無音・話者の重なり・分割境界・タイムスタンプのずれを評価する。
- モデル、量子化、whisper.cpp の版、スレッド数、VAD、OS、冷却条件、Gemma の常駐条件を記録する。
- `small` の優位や実時間以内の完了は未保証。`medium` / `large-v3` は初回評価の対象外とする。

## 未確定事項

Pi での VAD・分割境界・量子化・最終モデル、Gemma の入力長とロード方針、
P2 のライブ更新方式・翻訳キュー・資源制御、許容 STT 遅延・翻訳更新間隔・品質基準は各段階で決める。
ローカル自動テストと Windows CLI の短音声・録音中の部分結果テストは成功。Pi の録音成功はユーザー報告あり。
この環境からの Pi 実測、日本語品質・長時間性能・ライブ画面実操作は未検証。
