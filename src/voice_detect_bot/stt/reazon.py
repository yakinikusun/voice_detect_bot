import numpy as np

try:
    import sherpa_onnx
    from huggingface_hub import hf_hub_download
except ImportError as e:
    raise ImportError("ReazonSpeech backend requires 'sherpa-onnx' and 'huggingface-hub'") from e

MODEL_REPO = "reazon-research/reazonspeech-k2-v2"


class ReazonTranscriber:
    """sherpa-onnx + ReazonSpeech k2 v2 の文字起こしバックエンド"""

    def __init__(self, num_threads: int = 2):
        def dl(name: str) -> str:
            return hf_hub_download(repo_id=MODEL_REPO, filename=name)

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=dl("encoder-epoch-99-avg-1.int8.onnx"),
            decoder=dl("decoder-epoch-99-avg-1.onnx"),
            joiner=dl("joiner-epoch-99-avg-1.onnx"),
            tokens=dl("tokens.txt"),
            num_threads=num_threads,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            provider="cpu",
        )

    def transcribe(self, audio: np.ndarray) -> str:
        """1発話ごとにストリームを作って decode し、前後の空白を除いたテキストを返す"""
        if audio.size == 0:
            return ""

        audio = np.ascontiguousarray(audio, dtype=np.float32)
        stream = self._recognizer.create_stream()
        stream.accept_waveform(16000, audio)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()
