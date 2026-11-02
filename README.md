# voice-detect-bot

Discord のボイスチャンネル内の音声をリアルタイムで文字起こしし、指定したキーワード（NGワード）を検知して通知する Discord Bot です。

人間の在室状況に応じた**自動入退出**に対応しており、文字起こしバックエンドとして **ReazonSpeech**（sherpa-onnx）および **Whisper**（faster-whisper）を利用できます。

---

## 主な特徴

- 🎙️ **リアルタイム音声文字起こし**:
  - 発話ごとの無音検出と最大時間制限による自動セグメント化
  - 音声受信スレッドと推論スレッドを分離し、音飛びや遅延を抑制
- 🔍 **キーワード検知 & 通知**:
  - 設定したキーワード（NGワード）を検知した場合に、指定のテキストチャンネルへ即座に通知
  - 検知ログを `logs/` 配下のファイル（デフォルト: `logs/detected.log`）へ自動記録
  - 通知時のプッシュ通知有無（サイレント送信: `SILENT_MSG`）や、VC接続時のお知らせ通知（`CONNECT_NOTIFY`）を柔軟に切り替え可能
- 🚪 **自動入退出 & 監視連動**:
  - 対象のボイスチャンネルに人が入ると自動で接続して監視を開始
  - 誰もいなくなると自動で切断して待機
  - スラッシュコマンド（`/autojoin`）による監視・自動入退出の一括切り替え
  - Botのスピーカーミュート状態に応じたプレゼンス連動（ミュート時は「応答不可/dnd」、解除時は「オンライン」）
- 🛡️ **特権インテント不要 & 超軽量設計**:
  - **特権インテント (Privileged Gateway Intents) は一切不要**
  - 余計なチャットやタイピング等のイベントを遮断し、ボイス関連（`guilds` + `voice_states`）のみを処理するため低負荷でセキュア
- 🔄 **選べる STT バックエンド**:
  - **ReazonSpeech** (`sherpa-onnx`): 日本語に特化し、CPU環境でも非常に軽量・高速
  - **Whisper** (`faster-whisper`): 高精度な汎用文字起こしモデル（GPU/CPU両対応）

---

## 動作要件

- **Python**: 3.10 以上
- **システム依存ライブラリ**:
  - **`libopus`** (必須): Discord 音声パケットの送受信・デコードに必要です。
    ```bash
    # Debian / Ubuntu の場合
    sudo apt update && sudo apt install -y libopus0
    # CentOS / RHEL の場合
    sudo dnf install opus
    # macOS (Homebrew) の場合
    brew install opus
    ```
- **パッケージマネージャ**: [uv](https://docs.astral.sh/uv/)（推奨）または pip
- **Discord Bot**:
  - **特権インテント (Privileged Gateway Intents) は一切不要**（すべてOFFで動作可能）
    - `Message Content Intent`: 不要（スラッシュコマンドを使用するため）
    - `Server Members Intent`: 不要（ボイスステート情報で在室判定するため）
    - `Presence Intent`: 不要
  - Discord Developer Portal での特別なトグル設定や申請・審査なしですぐに導入できます。

---

## セットアップ手順

### 1. Discord Bot の作成とサーバーへの招待

1. **アプリケーションの作成**:
   - [Discord Developer Portal](https://discord.com/developers/applications) にアクセスし、右上の **「New Application」** をクリックします。
   - 任意の Bot 名を入力して作成します。

2. **Bot トークンの取得**:
   - 左側メニューの **「Bot」** を開きます。
   - **「Reset Token」** をクリックし、表示されたトークンをコピーして控えておきます（後ほど `.env` の `TOKEN` に設定します）。
   - > **Note**: **「Privileged Gateway Intents」のスイッチはすべて OFF のままで問題ありません。**

3. **Bot の招待リンク生成とサーバー追加**:
   - 左側メニューの **「OAuth2」 > 「URL Generator」** を開きます。
   - **SCOPES** で以下にチェックを入れます：
     - `bot`
     - `applications.commands` (スラッシュコマンドの利用に必須)
   - **BOT PERMISSIONS** で以下にチェックを入れます：
     - `Send Messages` (テキスト通知の送信)
     - `Connect` (ボイスチャンネルへの接続)
     - `Speak` (ボイスチャンネル参加)
   - ページ最下部に生成された **Generated URL** をコピーし、ブラウザでアクセスして Bot を導入したいサーバーに追加します。

---

### 2. リポジトリのクローン

```bash
git clone <リポジトリURL>
cd voice_detect_bot
```

---

### 3. 環境変数の設定

`.env.example` をコピーして `.env` を作成し、必要な値を入力します。

```bash
cp .env.example .env
```

> **Tip: チャンネルIDの確認方法**
> Discord の「ユーザー設定」>「詳細設定」で **開発者モード** を有効にすると、チャンネルを右クリックして「チャンネルIDをコピー」できるようになります。

#### 主な設定項目 (`.env`)

| 環境変数名 | 説明 | デフォルト値 / 例 |
| :--- | :--- | :--- |
| `TOKEN` | **[必須]** Discord Bot のトークン | `MTA...` |
| `VOICE_CHANNEL_ID` | **[必須]** 音声を受信するボイスチャンネル ID | `123456789012345678` |
| `NOTIFY_CHANNEL_ID` | キーワード検知通知を送るテキストチャンネル ID (空欄で無効化) | `123456789012345678` |
| `NG_WORDS` | 検知対象のキーワード（カンマ区切り。`KEYWORDS` も可） | `テスト,NGワード,注意` |
| `CONNECT_NOTIFY` | Bot がボイスチャンネルに接続した際に通知を送るか (`true` / `false`) | `false` |
| `SILENT_MSG` | メッセージ送信時にプッシュ通知を鳴らさないか (`true` / `false`) | `true` |
| `LOG_FILE` | 検知ログのファイル名（`logs/` ディレクトリ内に出力。空欄で無効化） | `detected.log` |
| `STT_BACKEND` | 文字起こしバックエンド (`reazon` または `whisper`) | `reazon` |
| `ASR_THREADS` | 推論に使用する CPU スレッド数 | `2` |

#### バックエンド別設定

**ReazonSpeech (`STT_BACKEND=reazon`) の場合:**
| 環境変数名 | 説明 | デフォルト値 |
| :--- | :--- | :--- |
| `REAZON_MODEL` | Hugging Face のモデルリポジトリ ID | `reazon-research/reazonspeech-k2-v2` |

**Whisper (`STT_BACKEND=whisper`) の場合:**
| 環境変数名 | 説明 | デフォルト値 |
| :--- | :--- | :--- |
| `WHISPER_MODEL` | モデルサイズ (`tiny`, `base`, `small`, `medium`, `large` など) | `small` |
| `WHISPER_DEVICE` | 実行デバイス (`cpu` または `cuda`) | `cpu` |
| `WHISPER_COMPUTE` | 演算精度 (`int8`, `float16` など) | `int8` |

#### 詳細チューニング項目 (省略時はデフォルト値を使用)

| 環境変数名 | 説明 | デフォルト値 |
| :--- | :--- | :--- |
| `SILENCE_SEC` | 発話終了と判定する無音継続時間 (秒) | `1.0` |
| `MIN_SEC` | これより短い発話は破棄する秒数 | `0.5` |
| `MAX_SEC` | 長すぎる発話を強制的に区切る秒数 | `20.0` |
| `MAX_PENDING` | 音声受信の最大待ち行列数 (滞留時のドロップ上限) | `5` |
| `WATCHDOG_SEC` | ボイスチャンネル状態の定期監視間隔 (秒) | `30` |

---

## 起動方法

`uv` を使用すると、使用するバックエンドに応じた依存関係のみをインストールして手軽に起動できます。

### ReazonSpeech を使用する場合 (推奨・デフォルト)

```bash
uv run --extra reazon voice-detect-bot
```

### Whisper を使用する場合

```bash
uv run --extra whisper voice-detect-bot
```

> **Note**: `pip` を利用する場合は `pip install -e ".[reazon]"` または `pip install -e ".[whisper]"` を実行後、`voice-detect-bot` コマンドで起動してください。

---

## スラッシュコマンド

Bot が起動すると、スラッシュコマンドツリーが自動的に同期されます。

### `/autojoin`
自動入退出と音声監視を一括で切り替えます（※サーバー管理者のみ実行可能）。

- **引数**: `enabled`
  - **`有効 (監視を開始し、人がいる時に自動接続)`**:
    - 自動入退出を有効化します。
    - 監視対象チャンネルに人間がいれば即座に接続し、音声の文字起こし・監視を開始します。
  - **`無効 (監視を停止し、VCから切断)`**:
    - 自動入退出を無効化します。
    - ボイスチャンネルから直ちに切断し、監視（パイプライン）を完全に停止します。

---

## ログファイル出力

キーワードを検知した場合、`logs/` ディレクトリ内に以下のフォーマットでリアルタイムに追記されます：

```text
[DETECTED] (15:04:05)  【ユーザー名】 says: `NGワード` in `発話の全文テキスト`
```

---

## プロジェクト構成

```
voice_detect_bot/
├── pyproject.toml               # プロジェクト定義・extras設定
├── .env.example                 # 環境変数サンプル
├── logs/                        # 検知ログ出力先ディレクトリ
├── doc/method_spec.md           # 関数・モジュール詳細仕様書
└── src/voice_detect_bot/
    ├── __init__.py
    ├── main.py                  # Bot初期化・スラッシュコマンド・エントリポイント
    ├── config.py                # 環境変数読み込み・Config定義
    ├── audio.py                 # PCM音声データ変換 (48kHz stereo -> 16kHz mono)
    ├── keywords.py              # キーワード検知
    ├── pipeline.py              # 発話キュー管理・推論・ログ記録・通知パイプライン
    ├── voice/
    │   ├── sink.py              # 発話単位でPCMをバッファリングする AudioSink
    │   └── autojoin.py          # 自動入退出・チャンネル状態監視 (VoiceManager)
    └── stt/
        ├── __init__.py          # Transcriber プロトコル・バックエンド生成ファクトリ
        ├── reazon.py            # ReazonSpeech バックエンド実装 (sherpa-onnx)
        └── whisper.py           # Whisper バックエンド実装 (faster-whisper)
```
