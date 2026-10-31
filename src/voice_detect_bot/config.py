from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """設定値の入れ物 (実行中に変更しない定数設定はすべて大文字)"""

    TOKEN: str
    VOICE_CHANNEL_ID: int
    NOTIFY_CHANNEL_ID: int
    STT_BACKEND: str  # reazon か whisper
    KEYWORDS: list[str]
    SILENCE_SEC: float = 1.0
    MIN_SEC: float = 0.5
    MAX_SEC: float = 20.0
    MAX_PENDING: int = 5
    WATCHDOG_SEC: int = 30
    LOG_FILE: str = "transcript.log"
    ASR_THREADS: int = 2
    REAZON_MODEL_REPO: str = "reazon-research/reazonspeech-k2-v2"
    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE: str = "int8"

    # 小文字でのアクセスも可能なようにプロパティを定義 (互換性担保)
    @property
    def token(self) -> str:
        return self.TOKEN

    @property
    def voice_channel_id(self) -> int:
        return self.VOICE_CHANNEL_ID

    @property
    def notify_channel_id(self) -> int:
        return self.NOTIFY_CHANNEL_ID

    @property
    def stt_backend(self) -> str:
        return self.STT_BACKEND

    @property
    def keywords(self) -> list[str]:
        return self.KEYWORDS

    @property
    def silence_sec(self) -> float:
        return self.SILENCE_SEC

    @property
    def min_sec(self) -> float:
        return self.MIN_SEC

    @property
    def max_sec(self) -> float:
        return self.MAX_SEC

    @property
    def max_pending(self) -> int:
        return self.MAX_PENDING

    @property
    def watchdog_sec(self) -> int:
        return self.WATCHDOG_SEC

    @property
    def log_file(self) -> str:
        return self.LOG_FILE

    @property
    def reason_model_repo(self) -> str:
        return self.REAZON_MODEL_REPO

    @property
    def asr_threads(self) -> int:
        return self.ASR_THREADS

    @property
    def whisper_model(self) -> str:
        return self.WHISPER_MODEL

    @property
    def whisper_device(self) -> str:
        return self.WHISPER_DEVICE

    @property
    def whisper_compute(self) -> str:
        return self.WHISPER_COMPUTE


def _parse_int_env(name: str, val: str | None) -> int:
    if val is None or not val.strip():
        raise ValueError(f"環境変数 {name} が設定されていません")
    try:
        return int(val.strip())
    except ValueError as e:
        raise ValueError(f"環境変数 {name} は整数である必要があります: {val}") from e


def load_config() -> Config:
    """環境変数から Config を作る"""
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN")
    if not token or not token.strip():
        raise ValueError("環境変数 DISCORD_TOKEN が設定されていません")

    voice_channel_id_raw = os.getenv("VOICE_CHANNEL_ID") or os.getenv("CHANNEL_ID")
    voice_channel_id = _parse_int_env("VOICE_CHANNEL_ID", voice_channel_id_raw)

    notify_channel_id_raw = os.getenv("NOTIFY_CHANNEL_ID")
    notify_channel_id = _parse_int_env("NOTIFY_CHANNEL_ID", notify_channel_id_raw)

    keywords_raw = os.getenv("KEYWORDS") or os.getenv("NG_WORDS", "")
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]

    stt_backend = os.getenv("STT_BACKEND", "reazon").strip().lower()

    silence_sec = float(os.getenv("SILENCE_SEC", "1.0"))
    min_sec = float(os.getenv("MIN_SEC", "0.5"))
    max_sec = float(os.getenv("MAX_SEC", "20.0"))
    max_pending = int(os.getenv("MAX_PENDING", "5"))
    watchdog_sec = int(os.getenv("WATCHDOG_SEC", "30"))
    log_file = os.getenv("LOG_FILE", "transcript.log").strip()
    asr_threads = int(os.getenv("ASR_THREADS", os.getenv("NUM_THREADS", "2")))
    reason_model_repo = os.getenv("MODEL_REPO", "reazon-research/reazonspeech-k2-v2").strip()
    whisper_model = os.getenv("WHISPER_MODEL", "small").strip()
    whisper_device = os.getenv("WHISPER_DEVICE", "cpu").strip()
    whisper_compute = os.getenv("WHISPER_COMPUTE", "int8").strip()

    return Config(
        TOKEN=token.strip(),
        VOICE_CHANNEL_ID=voice_channel_id,
        NOTIFY_CHANNEL_ID=notify_channel_id,
        STT_BACKEND=stt_backend,
        KEYWORDS=keywords,
        SILENCE_SEC=silence_sec,
        MIN_SEC=min_sec,
        MAX_SEC=max_sec,
        MAX_PENDING=max_pending,
        WATCHDOG_SEC=watchdog_sec,
        LOG_FILE=log_file,
        ASR_THREADS=asr_threads,
        WHISPER_MODEL=whisper_model,
        WHISPER_DEVICE=whisper_device,
        WHISPER_COMPUTE=whisper_compute,
        REAZON_MODEL_REPO=reason_model_repo,
    )
