# voice-detect-bot 関数仕様書

設計案(実装前) / srcレイアウト・uv run 前提 / 2026-10-31

## 共通方針

- 依存の向きは main → voice/autojoin → pipeline → (voice/sink, stt, audio, keywords) → config の一方通行。逆向きのimportは禁止
- スレッド: UtteranceSink.write だけがライブラリの受信スレッドから呼ばれる。推論は専用の1スレッド。それ以外はイベントループ上で動く
- モデルの読み込みはimport時ではなくコンストラクタで行う
- 外部要因の失敗(権限不足・タイムアウト・送信失敗)はログに残して継続し、プロセスは落とさない
- 音声の内部形式は、受信側が 48kHz / 16bit / ステレオのPCM、文字起こし側が 16kHz / モノラル / float32

## ファイル構造

```
voice_detect_bot/
├── pyproject.toml
├── .python-version
├── uv.lock
└── src/voice_detect_bot/
    ├── __init__.py
    ├── main.py        # main() をここに
    ├── config.py / pipeline.py / audio.py / keywords.py
    ├── voice/ (autojoin.py, sink.py)
    └── stt/ (__init__.py, reazon.py, whisper.py)
```

## config.py

### `Config`(frozen dataclass)

- 概要: 設定値の入れ物
- フィールド: token, voice\_channel\_id, notify\_channel\_id, stt\_backend(reazon か whisper), keywords(list\[str\]), silence\_sec=1.0, min\_sec=0.5, max\_sec=20.0, max\_pending=5, watchdog\_sec=30, log\_file=transcript.log, asr\_threads=2, whisper\_model=small, whisper\_device=cpu, whisper\_compute=int8

### `load_config() -> Config`

- 概要: 環境変数から Config を作る
- 必須: DISCORD\_TOKEN, VOICE\_CHANNEL\_ID, NOTIFY\_CHANNEL\_ID。KEYWORDS はカンマ区切り(前後の空白を除去し、空要素は捨てる)
- 例外: 必須変数がない、またはIDが整数でない場合は ValueError(変数名を含むメッセージ)

## audio.py

### `BYTES_PER_SEC = 192000`

- 概要: 受信PCMの1秒あたりのバイト数(48000 × 2byte × 2ch)

### `pcm_to_16k_mono(pcm: bytes) -> np.ndarray`

- 概要: 受信PCMを文字起こし用の形式に変換する
- 入力: 48kHz / 16bit / ステレオのPCM。長さが4の倍数でなくてもよい(端数は切り捨て)
- 出力: float32 / 16kHz / モノラル / 値域 -1〜1。空入力なら空配列
- 備考: 3サンプル平均による簡易ダウンサンプル。純粋関数で副作用なし

## keywords.py

### `normalize(s: str) -> str`

- 概要: 表記ゆれを減らした比較用の文字列を返す
- 処理: NFKC正規化 → 小文字化 → カタカナをひらがなに → 空白と句読点を除去
- 備考: 純粋関数

### `KeywordDetector(keywords: list[str])`

- 概要: キーワードを正規化して保持する。空のキーワードは無視する

### `KeywordDetector.detect(text: str) -> list[str]`

- 概要: text に含まれるキーワードを返す
- 出力: 元の表記のキーワードを設定順で返す(重複なし)。なければ空リスト
- 備考: 正規化後の部分一致

## stt/**init**.py

### `Transcriber`(Protocol)

- 概要: 文字起こしバックエンドの共通インターフェース
- メソッド: transcribe(audio: np.ndarray) -> str
- 入力: 16kHz / モノラル / float32
- 出力: 前後の空白を除いたテキスト。無音や認識なしは空文字
- 制約: 同時呼び出し不可(呼び出し側で1スレッドに直列化する)

### `create_transcriber(config: Config) -> Transcriber`

- 概要: stt\_backend に応じたバックエンドを生成する
- 処理: 使うバックエンドだけを関数内でimportする(未使用のライブラリが未インストールでも動くようにするため)
- 例外: 未知のバックエンドは ValueError。ライブラリ未インストールは、必要なextra名を含むメッセージで ImportError

## stt/reazon.py

### `ReazonTranscriber(num_threads: int)`

- 概要: sherpa-onnx + ReazonSpeech k2 v2 のバックエンド
- 処理: Hugging Faceからモデル4ファイル(encoder int8 / decoder / joiner / tokens)を取得し(初回のみダウンロード)、OfflineRecognizer.from\_transducer で greedy\_search の認識器を作る
- 例外: ダウンロードできず、キャッシュもない場合は例外をそのまま送出

### `ReazonTranscriber.transcribe(audio) -> str`

- 概要: 1発話ごとにストリームを作って decode し、テキストを返す
- 備考: 句読点は付かない。入力は float32 の連続配列に揃えてから渡す

## stt/whisper.py

### `WhisperTranscriber(model_size: str, device: str, compute_type: str)`

- 概要: faster-whisper のバックエンド。モデルを読み込む

### `WhisperTranscriber.transcribe(audio) -> str`

- 概要: 全セグメントを連結して返す
- 設定: language=ja, vad\_filter=True, beam\_size=1, temperature=0.0, without\_timestamps=True, condition\_on\_previous\_text=False

## voice/sink.py

### `Utterance`(dataclass)

- 概要: 1発話ぶんの音声
- フィールド: member(話者), started(最初のパケットの時刻, epoch秒), pcm(bytes, 48kHz / 16bit / ステレオ)

### `UtteranceSink(silence_sec: float, max_sec: float)`

- 概要: voice\_recv.AudioSink を継承し、ユーザーごとのバッファで発話を区切る
- 初期化: super().**init**() を呼び、ロックと、ユーザーID をキーにした各辞書(バッファ・開始時刻・最終受信時刻・メンバー)を用意する

### `UtteranceSink.wants_opus() -> bool`

- 概要: 常に False(デコード済みPCMで受け取る)

### `UtteranceSink.write(user, data) -> None`

- 概要: ライブラリが音声パケットごとに呼ぶ。呼び出し元スレッドは受信スレッド
- 処理: user が None・bot・PCMが空なら無視。それ以外はユーザーごとのバッファに追記し、初回なら開始時刻を記録、最終受信時刻を更新する(ロック内)
- 例外: 投げない

### `UtteranceSink.pop_finished() -> list[Utterance]`

- 概要: 発話が終わったものを取り出す。呼び出し元はイベントループ
- 条件: 最終受信から silence\_sec 以上経過、またはバッファが max\_sec 相当に達したユーザー
- 処理: 取り出したユーザーのバッファと時刻を削除する(ロック内)
- 備考: 順序は不定。必要なら呼び出し側で started 順に並べる

### `UtteranceSink.cleanup() -> None`

- 概要: ライブラリが受信停止時に呼ぶ。全バッファを破棄する

## pipeline.py

### `TranscriptionPipeline(config, transcriber, detector)`

- 概要: 発話の受信から、文字起こし・検知・通知までの流れを管理する
- 状態: 推論専用の ThreadPoolExecutor(max\_workers=1)、待ち件数 pending、workerのタスク、接続中の vc

### `start(vc: VoiceRecvClient, notify_channel: Messageable | None) -> None`

- 概要: 受信と文字起こしを開始する
- 処理: 古いworkerが残っていれば先に stop() し、新しい UtteranceSink を作って vc.listen() し、worker を起動する
- 備考: notify\_channel が None なら通知は行わず、ログのみ

### `stop() -> None`

- 概要: 受信と文字起こしを止める
- 処理: workerをcancelし、vc.stop\_listening() を呼ぶ(未受信でも例外にしない)
- 備考: 冪等。何度呼んでもよい

### `_worker(sink, notify_channel)`(内部)

- 概要: 0.3秒ごとに sink.pop\_finished() を確認する常駐タスク
- 処理: min\_sec 未満の発話は捨てる。残りを started 順に \_handle のタスクとして起動する。cancelされたら終了

### `_handle(utt, notify_channel)`(内部)

- 概要: 1発話の処理
- 処理: pending が max\_pending 以上なら警告ログを出して捨てる。そうでなければ、変換 → 推論スレッドで transcribe → 空なら終了 → ログ出力(形式: \[HH:MM:SS\] 名前: テキスト)と log\_file への追記 → キーワード検知 → 検知時のみ通知
- 例外: 推論が失敗したらログに残して終了。pending は必ず元に戻す

### `_notify(channel, content)`(内部)

- 概要: 通知を送る
- 処理: allowed\_mentions を無効にして送信。HTTPException はログに残して継続。channel が None なら何もしない

## voice/autojoin.py

### `VoiceManager(bot, config, pipeline)`

- 概要: 自動入退出と、botの状態監視を担当する
- 状態: \_voice\_lock(asyncio.Lock)、auto\_join\_enabled(初期値 True)、watchdog

### `sync_voice(guild) -> None`

- 概要: 対象チャンネルの人間の有無に合わせて、botの接続状態を揃える(冪等)
- 処理: ロックを取ったあとで、auto\_join\_enabled・人間の有無・guild.voice\_client を読む。OFFなら何もしない
- vc が None のとき: pipeline.stop() で残骸を整理する
- 人間がいる場合: 未接続なら connect(VoiceRecvClient) → pipeline.start → 入室の告知。別チャンネルにいれば move\_to
- 人間がいない場合: vc があれば pipeline.stop() → disconnect(どのチャンネルにいても)
- 例外: ClientException / Forbidden / TimeoutError はログに残して継続

### `set_auto_join(enabled: bool) -> None`

- 概要: 自動入退出のON/OFFを切り替える
- OFFのとき: ロックの内側で pipeline.stop() し、全ボイス接続を切断する
- ONのとき: 全ギルドに sync\_voice を呼び、OFFの間の変化に追いつく
- 備考: 同じ値で何度呼んでもよい。sync\_voice はロックを取るので、ロックの外から呼ぶ

### `start_watchdog() -> None`

- 概要: watchdog\_sec ごとに全ギルドへ sync\_voice を呼ぶ保険のループを開始する
- 処理: 開始前に bot.wait\_until\_ready() を待つ。二重起動はしない

### `handle_voice_state_update(member, before, after) -> None`

- 概要: on\_voice\_state\_update から委譲される
- 処理の順序: (1)bot自身の deaf が変化したら通知 (2)before.channel == after.channel なら終了 (3)対象チャンネルに無関係なら終了 (4)bot自身のイベントなら2秒待つ (5)sync\_voice
- 備考: 2秒待つのは、discord.py側の後片付けが終わる前だと guild.voice\_client が古いままになるため

### `_on_bot_deaf_changed(deafened: bool)`(内部)

- 概要: スピーカーミュートされた/解除されたことを、ログと通知に出す

## main.py

### `build_bot(config, transcriber) -> commands.Bot`

- 概要: Botを組み立てる(副作用なし。起動はしない)
- 処理: intents(デフォルト + message\_content)で Bot を作り、detector・pipeline・VoiceManager を組み立てて、on\_ready(watchdog開始)、on\_voice\_state\_update(VoiceManagerへ委譲)、!autojoin on/off(管理者のみ)を登録する

### `main() -> None`

- 概要: エントリポイント(pyproject.toml の project.scripts から呼ばれる)
- 処理: load\_config → ログ設定 → create\_transcriber → build\_bot → bot.run(token, log\_handler=None)

## 実機で確認が必要な点

- move\_to のあとも受信が続くか(続かない場合は、移動後に pipeline.start をやり直す)
- discord-ext-voice-recv と discord.py の版の組み合わせ、およびDAVE(E2EE)への対応
- サーバーデフ中の受信の挙動
- ReazonSpeech の長い発話の扱い(max\_sec の妥当な値)
