# 元の Gemma Translator とドイツ語

元の2人用翻訳画面は Pi の `http://localhost:3000` です。話している間キーを押し、離すと原文認識・翻訳・読み上げを行います。
今回追加した **German** も同じ画面・キー操作で使います。会議の録音中のライブ翻訳は別の後続機能です。

## Pi での更新・起動

この変更を含むソースを Pi へ反映し、翻訳サーバーを終了した状態で、リポジトリ内から実行します。

```bash
npm --prefix frontend run build
bash start.sh --prod
```

起動後、Chromium で `http://localhost:3000` を開いて再読み込みします。
会議画面の「翻訳モード ↗」もこの URL へのリンクで、翻訳サーバーを自動起動するものではありません。
既に systemd で翻訳を動かしている環境では、手動起動と重ねず既存のサービスを再起動してください。

会議の whisper.cpp が導入済みなら、追加の STT モデルは不要です。`start.sh` は `.local/meeting.env` を読み込み、
ドイツ語の認識に `WHISPER_BIN` / `WHISPER_MODEL` / `WHISPER_THREADS` / `WHISPER_TIMEOUT_SECONDS` 等を利用します。
別の設定ファイルは `MEETING_ENV_FILE` で指定できます。設定変更はサーバー再起動後に反映されます。
設定ファイルがなければ `.local/whisper.cpp/build/bin/whisper-cli` と `.local/models/ggml-base.bin` を既定で使います。

まだ whisper.cpp を導入していない場合だけ、先に実行します。

```bash
bash setup-meeting.sh --model base
```

使うモデルは多言語版の `ggml-base.bin` または `ggml-small.bin` です。`.en` が付く英語専用モデルはドイツ語に使えません。
`small` の追加方法と設定は [会議レコーダーの手順](meeting-recorder.md) を参照してください。

## キーボードで翻訳する

設定（⚙）の **Keyboard Mode → Landscape** を選んだ場合：

| キー | 操作 |
|---|---|
| Space | 操作する人を切り替える。四隅の印が選択中の側 |
| ← / → | 選択中の側の言語を変える |
| Z を押し続ける | 選択中の側の音声を録音する |
| Z を離す | 録音を停止して文字起こし・翻訳する |

1. 一方を **German**、もう一方を **Japanese** に設定する。
2. German 側を選択して Z を押しながら話し、話し終えたら離す。
3. ドイツ語の原文と日本語訳が表示され、**Enable Speech Output** がオンなら日本語を読み上げる。
4. 逆方向は Japanese 側を選んで同じ操作を行う。日本語を認識し、ドイツ語訳を表示・読み上げる。

**Vertical** 設定では1人目が Z、2人目が X。その他のキーは [README](../README.md#keyboard-shortcuts) を参照してください。
ブラウザにはマイクの使用を許可し、会議側の録音を停止してから試してください。元の翻訳は Chromium のマイクを使い、会議用の ALSA デバイス指定とは別です。

## 言語別の処理

| 入力・出力 | 使用する処理 |
|---|---|
| ドイツ語の音声入力 | whisper.cpp に `de` を指定して原文認識 |
| 既存6言語の音声入力 | 既存の Moonshine STT |
| 日本語・ドイツ語などへの翻訳 | 既存の Gemma / LiteRT-LM |
| ドイツ語の読み上げ | moonshine-voice の `TextToSpeech("de-de")` |
| 既存6言語の読み上げ | 従来の言語設定・音声 |

ドイツ語 STT は、キーを離したときの発話全体を処理します。会議用の10秒分割設定はこの操作には適用しません。
モデルが未配置の場合はエラーを表示し、英語認識への切り替えはしません。

`moonshine-voice==0.0.65` の内蔵カタログには `de-de` とドイツ語 Piper 音声が含まれています。
読み上げに必要な音声モデル・言語データは初回使用時に取得されるので、最初はネット接続が必要です。準備後の推論は端末内で行います。
事前準備をしたい場合は、翻訳サーバーを止めた状態で次を実行できます。スピーカーからの再生は行いません。

```bash
./venv/bin/python -c 'from moonshine_voice import TextToSpeech; tts = TextToSpeech("de-de"); audio, rate = tts.synthesize("Guten Tag."); print("German TTS ready:", len(audio), "samples at", rate, "Hz"); tts.close()'
```

参考: [Moonshine の TTS 説明](https://github.com/moonshine-ai/moonshine#texttospeech)、[固定している配布版](https://pypi.org/project/moonshine-voice/0.0.65/)。

## 実機 E2E

- German → Japanese と Japanese → German を短い発話で試す。原文・訳文・読み上げ言語を別々に確認する。
- 従来の English ↔ Japanese、Space / Z / X / 言語切り替え、TTS オフ、マイク許可を確認する。
- ドイツ語の人名・数字・固有名詞と、`base` / `small` の品質・処理時間を比較する。
- 初回モデル取得後、ネットワークを切って原文認識・翻訳・読み上げを確認する。
- 会議機能との同時使用時の CPU・メモリ・マイク競合は別途評価する。会議の録音・STT 優先のライブ翻訳はまだ実装していない。

ローカルでは HTTP の入力・応答・言語ルーティングと UI ビルドを検証した。認識器・TTS はテスト用に置き換えており、Pi のドイツ語認識・Gemma 翻訳品質・実際の読み上げ・画面実操作を検証したものではない。
