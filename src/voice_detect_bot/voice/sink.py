from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import discord
from discord.ext import voice_recv

from ..audio import BYTES_PER_SEC

logger = logging.getLogger(__name__)


@dataclass
class Utterance:
    """1発話ぶんの音声データ"""

    member: discord.abc.User | discord.Member
    started: float
    pcm: bytes


class UtteranceSink(voice_recv.AudioSink):
    """ユーザーごとのバッファで発話を区切る AudioSink。

    write() は音声受信スレッドから呼ばれるため、スレッドセーフにバッファリングする。
    """

    def __init__(self, silence_sec: float = 1.0, max_sec: float = 20.0):
        super().__init__()
        self.silence_sec = silence_sec
        self.max_sec = max_sec
        self._lock = threading.Lock()
        self._buf: dict[int, bytearray] = {}
        self._started: dict[int, float] = {}
        self._last: dict[int, float] = {}
        self._members: dict[int, discord.abc.User | discord.Member] = {}

    def wants_opus(self) -> bool:
        """デコード済みPCMで受け取るため常に False"""
        return False

    def write(self, user: Any, data: voice_recv.VoiceData) -> None:
        """音声パケットごとに呼ばれる (呼び出し元: 受信スレッド)。例外は投げない"""
        try:
            if user is None or getattr(user, "bot", False) or not data.pcm:
                return

            with self._lock:
                uid = user.id
                if uid not in self._buf:
                    self._buf[uid] = bytearray()
                    self._started[uid] = time.time()
                self._buf[uid].extend(data.pcm)
                self._last[uid] = time.monotonic()
                self._members[uid] = user
        except Exception:
            # 受信スレッドのクラッシュを防ぐため例外は投げない
            logger.exception("Error in UtteranceSink.write")

    def pop_finished(self) -> list[Utterance]:
        """発話が終了した(または長すぎる)ユーザーの音声を取り出す (呼び出し元: イベントループ)"""
        now = time.monotonic()
        finished: list[Utterance] = []
        max_bytes = self.max_sec * BYTES_PER_SEC

        with self._lock:
            for uid in list(self._buf):
                idle = now - self._last[uid]
                too_long = len(self._buf[uid]) >= max_bytes
                if idle >= self.silence_sec or too_long:
                    member = self._members.pop(uid)
                    started = self._started.pop(uid)
                    pcm = bytes(self._buf.pop(uid))
                    self._last.pop(uid, None)
                    finished.append(Utterance(member=member, started=started, pcm=pcm))

        return finished

    def cleanup(self) -> None:
        """全バッファを破棄する"""
        with self._lock:
            self._buf.clear()
            self._started.clear()
            self._last.clear()
            self._members.clear()
