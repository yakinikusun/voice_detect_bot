import numpy as np

BYTES_PER_SEC = 192000  # 48000 * 2byte * 2ch


def pcm_to_16k_mono(pcm: bytes) -> np.ndarray:
    """受信PCM(48kHz / 16bit / ステレオ)を文字起こし用(16kHz / モノラル / float32)に変換する。

    端数のバイトは切り捨て、3サンプル平均で簡易ダウンサンプリングする。
    """
    pcm = pcm[: len(pcm) // 4 * 4]
    if not pcm:
        return np.empty(0, dtype=np.float32)

    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    a = a.reshape(-1, 2).mean(axis=1)

    n = len(a) // 3 * 3
    if n == 0:
        return np.empty(0, dtype=np.float32)

    return a[:n].reshape(-1, 3).mean(axis=1)
