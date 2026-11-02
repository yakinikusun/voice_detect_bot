# voice-detect-bot 関数仕様書

実装仕様 / srcレイアウト・uv run 前提 / 2026-11-03 更新

## 共通方針

- **依存の向き**: `main` → `voice/autojoin` → `pipeline` → (`voice/sink`, `stt`, `audio`, `keywords`) → `config` の一方通行。逆向きのimportは禁止。
- **スレッドモデル**: `UtteranceSink.write` だけがライブラリの受信スレッドから呼ばれる。推論は専用の1スレッド（`ThreadPoolExecutor(max_workers=1)`）。それ以外は asyncio イベントループ上で動作。
- **モデルの初期化**: import時ではなく、各 Transcriber のコンストラクタで読み込む。
- **耐障害性**: 外部要因の失敗（権限不足・タイムアウト・HTTPエラー等）はログに残して継続し、プロセスは落とさない。
- **音声形式**:
  - 受信側: 48kHz / 16bit / ステレオ PCM
  - 文字起こし側: 16kHz / モノラル / float32 (値域: -1.0 〜 1.0)
- **Intents 最小化**: 特権インテント（Message Content, Server Members, Presence）は一切使用せず、`Intents.none()` から `guilds` と `voice_states` のみを使用。
- **コマンド仕様**: テキストコマンドは廃止し、スラッシュコマンド（`/rec_toggle`）に一本化。

---

## ファイル構造

```
voice_detect_bot/
├── pyproject.toml
├── .python-version
├── uv.lock
├── .env.example
├── README.md
├── doc/
│   └── method_spec.md    # 本仕様書
├── logs/                 # 検知ログ出力先
└── src/voice_detect_bot/
    ├── __init__.py
    ├── main.py           # Bot構築・スラッシュコマンド・main()
    ├── config.py         # Config 定義・load_config()
    ├── audio.py          # PCM音声変換 (48k stereo -> 16k mono)
    ├── keywords.py       # キーワード検知
    ├── pipeline.py       # 発話キュー管理・推論・ログ記録・通知パイプライン
    ├── voice/
    │   ├── __init__.py
    │   ├── sink.py       # 発話区切り AudioSink
    │   └── autojoin.py   # 自動入退出・チャンネル状態監視 (VoiceManager)
    └── stt/
        ├── __init__.py   # Transcriber Protocol・create_transcriber()
        ├── reazon.py     # ReazonSpeech バックエンド (sherpa-onnx)
        └── whisper.py    # Whisper バックエンド (faster-whisper)
```

---

## config.py

### `Config` (frozen dataclass)

- **概要**: 実行中に変更されない定数設定値の保持コンテナ（全フィールド大文字）。
- **フィールド**:
  | フィールド名 | 型 | デフォルト値 | 説明 |
  | :--- | :--- | :--- | :--- |
  | `TOKEN` | `str` | なし (必須) | Discord Bot トークン |
  | `VOICE_CHANNEL_ID` | `int` | なし (必須) | 音声を受信するボイスチャンネルID |
  | `NOTIFY_CHANNEL_ID` | `int` | なし (必須) | キーワード検知通知を送るテキストチャンネルID |
  | `KEYWORDS` | `list[str]` | なし | 検知対象キーワードリスト |
  | `STT_BACKEND` | `str` | なし | 文字起こしバックエンド (`reazon` または `whisper`) |
  | `CONNECT_NOTIFY` | `bool` | `True` | ボイスチャンネル接続時に通知を送るか |
  | `SILENT_MSG` | `bool` | `True` | 通知送信時にプッシュ通知を鳴らさないか (silent送信) |
  | `SILENCE_SEC` | `float` | `1.0` | 発話終了とみなす無音継続秒数 |
  | `MIN_SEC` | `float` | `0.5` | これより短い発話は破棄する秒数 |
  | `MAX_SEC` | `float` | `20.0` | 長すぎる発話を強制的に区切る秒数 |
  | `MAX_PENDING` | `int` | `5` | 推論待ちキューの最大滞留数 |
  | `WATCHDOG_SEC` | `int` | `30` | ボイス接続状態の定期監視間隔 (秒) |
  | `LOG_FILE` | `str` | `"detected.log"` | 検知ログファイル名 (`logs/` 配下に保存。空欄で無効化) |
  | `ASR_THREADS` | `int` | `2` | 推論に使用する CPU スレッド数 |
  | `REAZON_MODEL` | `str` | `"reazon-research/reazonspeech-k2-v2"` | ReazonSpeech のモデルリポジトリID |
  | `WHISPER_MODEL` | `str` | `"small"` | Whisper モデルサイズ |
  | `WHISPER_DEVICE` | `str` | `"cpu"` | Whisper 実行デバイス (`cpu` または `cuda`) |
  | `WHISPER_COMPUTE` | `str` | `"int8"` | Whisper 計算精度 (`int8`, `float16` など) |

### `_parse_int_env(name: str, val: str | None) -> int`

- **概要**: 環境変数の文字列を整数に変換する内部ヘルパー。
- **例外**: 未設定・空文字、または整数値にパースできない場合は `ValueError`（変数名を含むメッセージ）。

### `load_config() -> Config`

- **概要**: `.env` および環境変数から設定を読み込み `Config` を生成する。
- **必須変数**: `TOKEN` (または `DISCORD_TOKEN`), `VOICE_CHANNEL_ID`, `NOTIFY_CHANNEL_ID`。
- **後方互換対応**:
  - `TOKEN`: `DISCORD_TOKEN` または `TOKEN`
  - `KEYWORDS`: `KEYWORDS` または `NG_WORDS`（カンマ区切り、前後の空白除去、空要素除外）
  - `ASR_THREADS`: `ASR_THREADS` または `NUM_THREADS`
  - `REAZON_MODEL`: `REAZON_MODEL` または `MODEL_REPO`
- **例外**: 必須変数がない、または整数IDが不正な場合は `ValueError`。

---

## audio.py

### `BYTES_PER_SEC = 192000`

- **概要**: 受信PCMの1秒あたりのバイト数 ($48000 \text{ samples/sec} \times 2 \text{ bytes} \times 2 \text{ channels}$)。

### `pcm_to_16k_mono(pcm: bytes) -> np.ndarray`

- **概要**: 受信PCMを文字起こしモデル用形式にダウンサンプリング・変換する。
- **入力**: 48kHz / 16bit / ステレオ PCM (`bytes`)。4の倍数でない端数は切り捨て。
- **出力**: 16kHz / モノラル / float32 の `np.ndarray` (値域: $-1.0 \sim 1.0$)。空入力時は空配列。
- **処理**: 16bit int PCM を float32 化し、ステレオ平均 (2ch→1ch) 後、3サンプル平均で 48kHz→16kHz へ簡易ダウンサンプリング。純粋関数。

---

## keywords.py

### `normalize(s: str) -> str`

- **概要**: 表記ゆれを低減した比較用文字列を生成する純粋関数。
- **処理**: NFKC正規化 → 小文字化 → カタカナをひらがなに変換 (`ァ〜ヶ` → `ぁ〜ゖ`) → Unicode句読点 (P) および空白文字を除去。

### `KeywordDetector(keywords: list[str])`

- **概要**: 検出対象キーワードの初期化・保持。
- **処理**: 前後の空白を除去し、空要素や重複を排除して登録。

### `KeywordDetector.detect(text: str) -> list[str]`

- **概要**: テキスト内に含まれるキーワードを検知する。
- **判定**: `text` に対する部分一致。
- **出力**: 検知された元の表記のキーワードを設定順（重複なし）のリストで返す。未検知時は空リスト。

---

## stt/__init__.py

### `Transcriber` (Protocol)

- **概要**: 文字起こしバックエンドの共通プロトコル。
- **メソッド**: `transcribe(audio: np.ndarray) -> str`
  - **入力**: 16kHz / モノラル / float32 の `np.ndarray`。
  - **出力**: 前後の空白を除去した認識結果テキスト。無音や認識失敗時は空文字。

### `create_transcriber(config: Config) -> Transcriber`

- **概要**: `config.STT_BACKEND` に応じた文字起こしバックエンドインスタンスを生成する。
- **遅延インポート**: 指定されたバックエンドのみを関数内でインポートし、使わないバックエンドの依存ライブラリ（`sherpa-onnx` や `faster-whisper`）が未インストールでも動作可能にする。
- **分岐**:
  - `"reazon"`: `ReazonTranscriber(num_threads=config.ASR_THREADS, model_repo=config.REAZON_MODEL)`
  - `"whisper"`: `WhisperTranscriber(model_size=config.WHISPER_MODEL, device=config.WHISPER_DEVICE, compute_type=config.WHISPER_COMPUTE)`
- **例外**: 未知のバックエンドは `ValueError`。依存ライブラリ未インストール時は `ImportError`。

---

## stt/reazon.py

### `ReazonTranscriber(num_threads: int = 2, model_repo: str = "reazon-research/reazonspeech-k2-v2")`

- **概要**: sherpa-onnx + ReazonSpeech k2 v2 の文字起こしバックエンド。
- **初期化**: Hugging Face Hub からモデルファイル4種（`encoder-epoch-99-avg-1.int8.onnx`, `decoder-epoch-99-avg-1.onnx`, `joiner-epoch-99-avg-1.onnx`, `tokens.txt`）を取得・キャッシュし、`sherpa_onnx.OfflineRecognizer.from_transducer` で greedy_search 認識器を構築。

### `ReazonTranscriber.transcribe(audio: np.ndarray) -> str`

- **概要**: 連続配列化 (`np.ascontiguousarray`) した音声を `OfflineRecognizer` のストリームに流し、デコード結果テキストを返す。

---

## stt/whisper.py

### `WhisperTranscriber(model_size: str = "small", device: str = "cpu", compute_type: str = "int8")`

- **概要**: faster-whisper の文字起こしバックエンド。
- **初期化**: `faster_whisper.WhisperModel` を指定されたパラメータでロード。

### `WhisperTranscriber.transcribe(audio: np.ndarray) -> str`

- **概要**: 音声を文字起こしし、全セグメントのテキストを連結して返す。
- **推論設定**: `language="ja"`, `vad_filter=True`, `beam_size=1`, `temperature=0.0`, `without_timestamps=True`, `condition_on_previous_text=False`。

---

## voice/sink.py

### `Utterance` (dataclass)

- **概要**: 1発話分の音声データコンテナ。
- **フィールド**:
  - `member`: `discord.abc.User | discord.Member` (話者)
  - `started`: `float` (発話開始時刻, epoch秒)
  - `pcm`: `bytes` (48kHz / 16bit / ステレオ PCM)

### `UtteranceSink(silence_sec: float = 1.0, max_sec: float = 20.0)`

- **概要**: `voice_recv.AudioSink` を継承し、ユーザーごとの音声パケットを受信・バッファリングして発話単位に区切る。
- **`wants_opus() -> bool`**: 常に `False` (PCMで受信)。
- **`write(user, data: voice_recv.VoiceData) -> None`**:
  - 音声受信スレッドからパケットごとに呼び出される。
  - `user` が None、Bot、または PCM が空なら破棄。それ以外はスレッドロック内でバッファに蓄積し、初回受信時刻と最終更新時刻を記録。
  - 例外は一切送出しない（クラッシュ防止）。
- **`pop_finished() -> list[Utterance]`**:
  - イベントループ側から定期的に呼び出される。
  - 最終パケット受信から `silence_sec` 以上経過、またはバッファが `max_sec * BYTES_PER_SEC` に達したユーザーの発話を取り出し、バッファから削除する。
- **`cleanup() -> None`**: 全バッファ・タイムスタンプを破棄。

---

## pipeline.py

### `TranscriptionPipeline(config: Config, transcriber: Transcriber, detector: KeywordDetector)`

- **概要**: 発話の受信から、文字起こし・キーワード検知・ログ保存・Discord通知までのパイプライン全体を管理。
- **状態**: `_executor` (`ThreadPoolExecutor(max_workers=1)`), `_pending` (推論待ち件数), `_worker_task` (監視タスク), `_vc` (接続中クライアント)。

### `start(vc: VoiceRecvClient, notify_channel: Messageable | None) -> None`

- **概要**: 音声受信と文字起こし worker タスクを開始する。既存 worker があれば先に `stop()` する。

### `stop() -> None`

- **概要**: worker タスクをキャンセルし、ボイス受信 (`vc.stop_listening()`) を停止する（冪等）。

### `_worker(sink: UtteranceSink, notify_channel: Messageable | None)` (内部)

- **概要**: 0.3秒間隔で `sink.pop_finished()` をポーリングする常駐タスク。
- **処理**: `MIN_SEC` 未満の短すぎる音声は破棄し、残りを `started` 順にソートして `_handle` タスクを並行起動。

### `_handle(utt: Utterance, notify_channel: Messageable | None)` (内部)

- **概要**: 1発話の処理フロー。
  1. `pending >= MAX_PENDING` の場合は滞留警告ログを出して発話をドロップ。
  2. `pcm_to_16k_mono` で変換し、推論専用スレッドで `transcriber.transcribe` を実行。
  3. テキストが空なら終了。
  4. 通常発話ログを出力 (`logger.debug("[HH:MM:SS] 名前: テキスト")`)。
  5. キーワード検知 (`detector.detect(text)`)。
  6. キーワード検知時:
     - `logger.info("Keywords found in ...")` を出力。
     - `LOG_FILE` が指定されていれば、`logs/{LOG_FILE}` に以下形式で追記:
       `[DETECTED] (HH:MM:SS)  【名前】 says: `キーワード` in `全文``
     - `_notify` で通知チャンネルへ送信。

### `_notify(channel: Messageable | None, content: str)` (内部)

- **概要**: 通知を送信する。
- **設定**: `allowed_mentions=none`, `silent=config.SILENT_MSG`。`HTTPException` はログに残して継続。

---

## voice/autojoin.py

### `VoiceManager(bot: discord.Client, config: Config, pipeline: TranscriptionPipeline)`

- **概要**: ボイスチャンネルへの自動入退出、接続維持、スピーカーミュート監視、Botステータス管理を担当。

### `update_presence(guild: discord.Guild | None = None) -> None`

- **概要**: 監視の有効/無効およびスピーカーミュート状態を一元評価し、Botのプレゼンスステータスを更新する。
- **判定ルール**:
  - `is_deaf`: Bot がボイスチャンネルに接続中で、かつ `guild.me.voice.deaf` または `guild.me.voice.self_deaf` が True の場合。
  - **`auto_join_enabled` かつ `not is_deaf`**: `discord.Status.online`
  - **それ以外 (監視OFF または スピーカーミュート中)**: `discord.Status.do_not_disturb` (dnd)

### `sync_voice(guild: discord.Guild) -> None`

- **概要**: 対象ボイスチャンネルに人間がいるかどうかに応じて、Botの接続状態を揃える（冪等）。
- **処理**:
  - `auto_join_enabled` が False の場合は何もしない。
  - 対象チャンネルに人間がいる場合:
    - 未接続なら `channel.connect(self_mute=True, cls=VoiceRecvClient)` → `pipeline.start`。`CONNECT_NOTIFY` が True なら入室告知メッセージを送信。
    - 別のボイスチャンネルにいる場合は `vc.move_to(channel)`。
  - 人間がいない場合:
    - 接続中なら `pipeline.stop()` → `vc.disconnect()`。

### `set_auto_join(enabled: bool, guild: discord.Guild | None = None) -> None`

- **概要**: 自動入退出・監視の有効/無効を一括切り替え。
- **OFF時**: ロック内で `pipeline.stop()` し、全ボイス接続を切断。
- **ON時**: 全ギルドに対して `sync_voice` を呼び出して即時同期。
- **共通**: 最後に `update_presence(guild)` を呼び出してステータスを整合。

### `start_watchdog() -> None`

- **概要**: `WATCHDOG_SEC` 秒ごとに全ギルドへ `sync_voice` を実行する定期監視ループを開始（二重起動防止あり）。

### `handle_voice_state_update(member, before, after) -> None`

- **概要**: `on_voice_state_update` から委譲されるイベントハンドラ。
- **順序**:
  1. Bot 自身の `deaf` 変化を検知したら `_on_bot_deaf_changed` を呼び出し。
  2. 同一チャンネル内の変化（ミュート等）は無視。
  3. 対象ボイスチャンネルに関係のない移動は無視。
  4. Bot 自身の移動イベントなら 2 秒待機（discord.py の状態反映待ち）。
  5. `sync_voice(member.guild)` を実行。

### `_on_bot_deaf_changed(guild: discord.Guild, deafened: bool) -> None`

- **概要**: Botのスピーカーミュート状態が変化した際の処理。
- **処理**:
  - `update_presence(guild)` でステータスを即座に連動。
  - 通知チャンネルへ警告/復帰メッセージを送信。
  - `deafened` なら `pipeline.stop()`、解除されたら `pipeline.start(vc, notify_channel)` で受信再開。

---

## main.py

### `_IgnoreRtcp(logging.Filter)`

- **概要**: `voice-recv` ライブラリの `unexpected rtcp packet` などの警告ノイズログを除去するフィルタ。

### `setup_logging() -> None`

- **概要**: `voice_detect_bot` パッケージ専用ロガーにフォーマッタ付き `StreamHandler` を登録し、`propagate = False` に設定。外部ライブラリ（`discord.py` 等）の大量の INFO ログがコンソールに流れるのを完全に遮断する。

### `build_bot(config: Config, transcriber: Transcriber) -> discord.Client`

- **概要**: Discord クライアントおよびスラッシュコマンドツリーを組み立てる（副作用なし、起動はしない）。
- **Intents 設定**:
  ```python
  intents = discord.Intents.none()
  intents.voice_states = True
  intents.guilds = True
  ```
- **登録イベント**:
  - `on_ready`: watchdog開始、`update_presence()` による初期ステータス反映、`tree.sync()` によるスラッシュコマンド同期。
  - `on_voice_state_update`: `voice_manager.handle_voice_state_update` へ委譲。
- **登録コマンド**:
  - `/rec_toggle` (スラッシュコマンド, 管理者限定):
    - 引数 `enabled`: `on` (有効化) / `off` (無効化)
    - `voice_manager.set_auto_join(True/False, guild=guild)` を呼び出し。
    - 実行結果メッセージ（ephemeral）および通知チャンネルへの告知メッセージを送信。

### `main() -> None`

- **概要**: エントリポイント (`pyproject.toml` の `voice-detect-bot` コマンドから呼び出し)。
- **処理**:
  1. `load_config()` で設定読み込み。
  2. `setup_logging()` でログ初期化。
  3. `Path("logs").mkdir(parents=True, exist_ok=True)` でログディレクトリ作成。
  4. `create_transcriber(config)` で STT バックエンド生成。
  5. `build_bot(config, transcriber)` でクライアント生成。
  6. `bot.run(config.TOKEN, log_handler=None)` で起動。
