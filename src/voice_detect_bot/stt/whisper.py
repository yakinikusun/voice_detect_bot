import numpy as np

try:
    from faster_whisper import WhisperModel
except ImportError as e:
    raise ImportError("Whisper backend requires 'faster-whisper'") from e


class WhisperTranscriber:
    """faster-whisper の文字起こしバックエンド"""

    def __init__(self, model_size: str = "small", device: str = "cpu", compute_type: str = "int8"):
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, audio: np.ndarray) -> str:
        """全セグメントを連結して前後の空白を除いたテキストを返す"""
        if audio.size == 0:
            return ""

        audio = np.ascontiguousarray(audio, dtype=np.float32)
        segments, _ = self._model.transcribe(
            audio,
            language="ja",
            vad_filter=True,
            beam_size=1,
            temperature=0.0,
            without_timestamps=True,
            condition_on_previous_text=False,
        )
        texts = [seg.text for seg in segments]
        return "".join(texts).strip()
