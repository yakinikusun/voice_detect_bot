from __future__ import annotations

from typing import Protocol
import numpy as np

from ..config import Config


class Transcriber(Protocol):
    """文字起こしバックエンドの共通インターフェース"""

    def transcribe(self, audio: np.ndarray) -> str:
        """16kHz / モノラル / float32 の音声を文字起こしし、テキストを返す"""
        ...


def create_transcriber(config: Config) -> Transcriber:
    """stt_backend に応じたバックエンドを生成する。

    使うバックエンドだけを関数内で import することで、未使用の依存ライブラリが
    未インストールでも動作するようにする。
    """
    backend = config.STT_BACKEND.lower()

    if backend == "reazon":
        try:
            from .reazon import ReazonTranscriber

            return ReazonTranscriber(num_threads=config.ASR_THREADS)
        except ImportError as e:
            raise ImportError(
                "reazon バックエンドの依存関係が見つかりません。必要なライブラリ(sherpa-onnx, huggingface-hub 等)をインストールしてください。"
            ) from e

    elif backend == "whisper":
        try:
            from .whisper import WhisperTranscriber

            return WhisperTranscriber(
                model_size=config.WHISPER_MODEL,
                device=config.WHISPER_DEVICE,
                compute_type=config.WHISPER_COMPUTE,
            )
        except ImportError as e:
            raise ImportError(
                "whisper バックエンドの依存関係が見つかりません。'faster-whisper' をインストールしてください。"
            ) from e

    else:
        raise ValueError(f"未知の STT バックエンドです: {config.STT_BACKEND}")

