# 会議レコーダー（P1）

Raspberry Pi のマイクから音声を保存し、停止後に whisper.cpp で文字起こしする機能です。
既存の翻訳アプリは従来どおり起動できます。会議用サーバーは別プロセス・別ポートで、Gemma / Moonshine は読み込みません。
要約・TODO の自動生成とドイツ語のライブ翻訳は後続フェーズです。

## Raspberry Pi へのインストール

対象は Raspberry Pi 5 / 8GB、64-bit Raspberry Pi OS、USB マイクです。
この実装では `arecord` を使用し、**操作している PC のマイクではなく、Pi に接続したマイク**を録音します。

この変更を含むソースを Pi に配置したら、通常のデスクトップユーザーでリポジトリ内から実行します。
スクリプト全体には sudo を付けません。OS パッケージの導入時だけ内部で sudo を使います。

```bash
bash setup-meeting.sh --model base
bash start-meeting.sh --list-devices
bash start-meeting.sh --check
bash start-meeting.sh
```

Pi の Chromium で **http://localhost:3001** を開きます。既存翻訳アプリの設定画面にも会議レコーダーへのリンクがあります。
既存の `start.sh` / `deploy-pi.sh` / systemd / kiosk の設定は変更しません。
会議サーバーの終了は起動ターミナルで Ctrl+C。進行中の録音は保存し、文字起こしは中断状態にします。

インストーラーは OS パッケージ、whisper.cpp のビルド、多言語版モデル、UI の依存関係とビルドを準備します。
whisper.cpp は `b4938` に固定し、CMake のビルド並列数は2、推論スレッド数は3を初期値にしています。
版と CLI 仕様の根拠: [上流リリース](https://github.com/ggml-org/whisper.cpp/releases/tag/b4938)、
[CLI ソース](https://github.com/ggml-org/whisper.cpp/blob/b4938/examples/cli/cli.cpp)。
初回はネット接続が必要です。準備後、会議サーバーの起動・録音・文字起こしはオフラインで行えます。

既に OS パッケージが揃っていれば `--skip-system-packages` を指定できます。
`small` も比較する場合は `bash setup-meeting.sh --model small` を実行し、下記設定の `WHISPER_MODEL` を変更します。
再実行時は既存設定を上書きしないため、モデルの追加と選択は別操作です。

## マイクと設定

設定は自動生成される `.local/meeting.env` にあります。主な値は次のとおりです。

| 設定 | 初期値・意味 |
|---|---|
| `MEETING_AUDIO_DEVICE` | `default`。`arecord -L` にあるデバイス名 |
| `WHISPER_MODEL` | `.local/models/ggml-base.bin` の絶対パス |
| `WHISPER_THREADS` | `3` |
| `MEETING_CHUNK_SECONDS` | `300`。文字起こし1区間の長さ |
| `WHISPER_TIMEOUT_SECONDS` | `1800`。1区間の処理上限 |
| `MEETING_MAX_SECONDS` | `7200`。録音・音声取り込みの上限（性能保証ではない） |
| `MEETING_DATA_DIR` | `.local/meetings` の絶対パス |
| `MEETING_HOST` / `MEETING_PORT` | `127.0.0.1` / `3001` |

USB マイクを明示するには `.local/meeting.env` 内の行を `export MEETING_AUDIO_DEVICE='plughw:CARD=実際のカード名,DEV=0'` のように変更します。
`plughw` は16 kHz / mono / S16_LEへの変換に利用できます。名前は実機の `arecord -L` で確認してください。
設定変更後は会議サーバーを再起動します。ログイン中のデスクトップユーザーで実行すると既存の音声環境を使いやすくなります。

別PCから確認する場合は、Pi へSSHでポート転送してそのPCの `http://localhost:3001` を開けます。
録音対象は引き続き Pi のマイクです。既定では LAN 全体に録音操作を公開しません。

```bash
ssh -L 3001:127.0.0.1:3001 user@raspberrypi
```

## 使い方と保存

1. 会議名・話す言語を選び「録音を開始」を押す。
2. 終了時に「録音を停止」を押し、保存完了を待つ。
3. 「文字起こしを開始・再開」を押す。区間ごとに結果が更新される。
4. 音声・文字起こしをダウンロードする。不要な会議は確認画面から削除できる。

録音はブラウザを閉じても続きます。停止操作は再度画面を開いて行えます。
1つの会議サーバーで録音または文字起こしを1件ずつ行い、翻訳アプリと同時にマイクを使う運用は実機で確認してください。

```text
.local/meetings/<一意な会議ID>/
  metadata.json     # 開始日時・言語・状態・進捗・処理時間
  audio.pcm         # 逐次保存する復旧用の生音声（マイク録音時）
  audio.wav         # 停止後に確定する 16 kHz / mono / 16-bit PCM WAV
  transcript.json   # 原文区間の ID・開始秒・終了秒
  transcript.md     # 人が読む文字起こし
  chunks/           # 再開用の区間結果
```

音声・モデル・機器設定を含む `.local/` 全体は Git の対象外です。
1時間の音声は PCM 約115 MB、WAV 約115 MB（計約230 MB）に加え、文字起こし中の一時ファイルを使用します。
容量が少なくなったら録音を止めて保存を試みます。空き容量256 MiBを下限とし、WAV確定用の容量も確保します。
電源断時は書き込み済み PCM の区間を次回起動時に WAV へ復旧します。未書き込みの音声や媒体故障まで保証するものではありません。
保存音声・モデル・分割条件を変えた場合は区間結果を作り直します。

## 既存 WAV の文字起こし・モデル比較

会議サーバーを終了してから実行してください。同じ保存先への二重起動は拒否します。

```bash
bash start-meeting.sh --import-wav /path/to/meeting.wav --language ja
```

入力は16 kHz / モノラル / 16-bit PCM WAVです。必要なら別途 ffmpeg で変換します。

```bash
ffmpeg -i input.wav -ar 16000 -ac 1 -c:a pcm_s16le meeting.wav
```

同じ音声を `base` / `small` で比較し、`metadata.json` の `processing_seconds`、日本語の固有名詞・数字・分割境界を確認します。
初期設定の分割は連続した300秒ごとで、モデルは各区間で読み込みます。境界の認識精度・速度はPiでの評価が必要です。
VADは `WHISPER_VAD_MODEL` に別途配置した対応モデルを指定すると有効になります。初回導入ではVADモデルを自動取得しません。

## Raspberry Pi での E2E 確認

会議 UI・API・whisper.cpp を接続した段階で実機 E2E が必要です。PC の自動テストだけでは ALSA・USB マイク・発熱・認識品質は確認できません。

- **まず30秒**: 機器確認 → 録音 → 停止 → 文字起こし → ダウンロード。録音を再生して実際の声が入っていることを確認する。
- **次に10分、最後に60分**: ブラウザの再読込・閉じて再接続、録音の継続、進捗、タイムスタンプ、区間境界の欠落・重複を確認する。
- **中断と再開**: 文字起こしを中断し、同じ条件で再実行。確定済み区間が再利用されることを確認する。
- **障害時**: USB マイク切断、会議サーバーの停止・再起動を試す。保存済み音声を再処理できることを確認する。容量不足は自動テストでも確認する。
- **負荷とオフライン**: `top` / `free -h` / `vcgencmd measure_temp` と処理時間を記録し、ネットワークを切って同じ流れを確認する。
- **既存機能**: 従来の `start.sh --prod` で2人用翻訳、対応言語、キー操作、TTS、設定が保たれているか確認する。

記録には Pi / OS / マイク / 冷却 / モデル / 版 / スレッド数 / 音声長 / 処理時間を含めてください。
`base` と `small` の採否、60分の実用性、境界精度はこの結果で判断します。

## 開発時の確認

2026-09-05 時点で Windows の自動テスト21件、両画面のビルド、Bash 構文検査が成功しています。
公式 Windows CLI `b4938` + 多言語 `base` でも公開サンプル11秒の分割認識・保存を確認しました。
Pi のインストール実行・実マイク・日本語の認識品質・長時間性能・画面実操作は未確認です。

```bash
python3 -m unittest discover -s tests -v
npm --prefix frontend run build
bash -n setup-meeting.sh
bash -n start-meeting.sh
```

UI 開発では会議サーバーを起動した上で `npm --prefix frontend run dev` を実行し、
`http://localhost:5173/meeting.html` を開きます。既存 API のプロキシは維持し、会議 API だけ3001へ送ります。
