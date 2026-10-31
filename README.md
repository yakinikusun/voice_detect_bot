# voice-detect-bot

Discord のボイスチャンネル内の音声をリアルタイムで文字起こしし、指定したキーワードを検知して通知する Discord Bot です。

人間の在室状況に応じた**自動入退出**に対応しており、文字起こしバックエンドとして **ReazonSpeech**（sherpa-onnx）および **Whisper**（faster-whisper）を利用できます。

---

## 主な特徴

- 🎙️ **リアルタイム音声文字起こし**:
  - 発話ごとの無音検出と最大時間制限による自動セグメント化
  - 音声受信スレッドと推論スレッドを分離し、音飛びや遅延を抑制
- 🔍 **キーワード検知 & 通知**:
  - 設定したキーワード（NGワードなど）を検知した場合に、指定のテキストチャンネルへ即座に通知
  - 文字起こしログのコンソール表示およびファイル（`transcript.log`）へのリアルタイム追記
- 🚪 **自動入退出 & 監視連動**:
  - 対象のボイスチャンネルに人が入ると自動で接続して監視を開始
  - 誰もいなくなると自動で切断して待機
  - スラッシュコマンド（`/autojoin`）による監視・自動入退出の一括切り替え
- 🔄 **選べる STT バックエンド**:
  - **ReazonSpeech** (`sherpa-onnx`): 日本語に特化し、CPU環境でも非常に軽量・高速
  - **Whisper** (`faster-whisper`): 高精度な汎用文字起こしモデル（GPU/CPU両対応）

---

## 動作要件

- **Python**: 3.10 以上
- **パッケージマネージャ**: [uv](https://docs.astral.sh/uv/)（推奨）または pip
- **Discord Bot**:
  - Privileged Gateway Intents:
    - **Server Members Intent** (有効化必須: ボイスチャンネル内のメンバー検出に使用)
    - **Message Content Intent** (推奨)

---

## セットアップ手順

### 1. リポジトリのクローン

```bash
git clone <リポジトリURL>
cd voice_detect_bot
```

### 2. 環境変数の設定

`.env.example` をコピーして `.env` を作成し、必要な値を入力します。

```bash
cp .env.example .env
```

#### 主な設定項目 (`.env`)

| 環境変数名 | 説明 | デフォルト値 / 例 |
| :--- | :--- | :--- |
| `TOKEN` (または `DISCORD_TOKEN`) | **[必須]** Discord Bot のトークン | `MTA...` |
| `CHANNEL_ID` (または `VOICE_CHANNEL_ID`) | **[必須]** 監視対象のボイスチャンネル ID | `123456789012345678` |
| `NOTIFY_CHANNEL_ID` | **[必須]** キーワード検知通知を送るテキストチャンネル ID | `123456789012345678` |
| `KEYWORDS` (または `NG_WORDS`) | 検知対象のキーワード（カンマ区切り） | `テスト,NGワード,注意` |
| `SILENT` | 通知送信時にプッシュ通知を鳴らさないか (`true` / `false`) | `true` |
| `STT_BACKEND` | 文字起こしバックエンド (`reazon` または `whisper`) | `reazon` |
| `ASR_THREADS` | 推論に使用する CPU スレッド数 | `2` |

#### バックエンド別設定

**ReazonSpeech (`STT_BACKEND=reazon`) の場合:**
| 環境変数名 | 説明 | デフォルト値 |
| :--- | :--- | :--- |
| `MODEL_REPO` | Hugging Face のモデルリポジトリ ID | `reazon-research/reazonspeech-k2-v2` |

**Whisper (`STT_BACKEND=whisper`) の場合:**
| 環境変数名 | 説明 | デフォルト値 |
| :--- | :--- | :--- |
| `WHISPER_MODEL` | モデルサイズ (`tiny`, `base`, `small`, `medium`, `large-v3`) | `small` |
| `WHISPER_DEVICE` | デバイス (`cpu` または `cuda`) | `cpu` |
| `WHISPER_COMPUTE` | 演算精度 (`int8`, `float16` など) | `int8` |

#### 詳細チューニング項目 (省略時はデフォルト値を使用)

| 環境変数名 | 説明 | デフォルト値 |
| :--- | :--- | :--- |
| `SILENCE_SEC` | 発話終了と判定する無音継続時間 (秒) | `1.0` |
| `MIN_SEC` | これより短い発話は破棄する秒数 | `0.5` |
| `MAX_SEC` | 長すぎる発話を強制的に区切る秒数 | `20.0` |
| `MAX_PENDING` | 文字起こしキューの最大保持数 (滞留時のドロップ上限) | `5` |
| `WATCHDOG_SEC` | ボイスチャンネル状態の定期巡回間隔 (秒) | `30` |
| `LOG_FILE` | 文字起こしログの保存先ファイルパス | `transcript.log` |

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

## プロジェクト構成

```
voice_detect_bot/
├── pyproject.toml               # プロジェクト定義・extras設定
├── .env.example                 # 環境変数サンプル
├── spec.md                      # 関数・モジュール詳細仕様書
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
