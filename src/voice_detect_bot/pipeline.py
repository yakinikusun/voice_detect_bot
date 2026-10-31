from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import discord
from discord.abc import Messageable

from .audio import BYTES_PER_SEC, pcm_to_16k_mono
from .config import Config
from .keywords import KeywordDetector
from .stt import Transcriber
from .voice.sink import Utterance, UtteranceSink

if TYPE_CHECKING:
    from discord.ext.voice_recv import VoiceRecvClient

logger = logging.getLogger(__name__)


class TranscriptionPipeline:
    """発話の受信から、文字起こし・検知・通知までの流れを管理するパイプライン"""

    def __init__(self, config: Config, transcriber: Transcriber, detector: KeywordDetector):
        self._config = config
        self._transcriber = transcriber
        self._detector = detector

        self._executor = ThreadPoolExecutor(max_workers=1)
        self._pending = 0
        self._worker_task: asyncio.Task | None = None
        self._vc: VoiceRecvClient | None = None

    def start(self, vc: VoiceRecvClient, notify_channel: Messageable | None) -> None:
        """受信と文字起こしを開始する"""
        self.stop()

        self._vc = vc
        sink = UtteranceSink(
            silence_sec=self._config.SILENCE_SEC,
            max_sec=self._config.MAX_SEC,
        )
        self._vc.listen(sink)
        self._worker_task = asyncio.create_task(self._worker(sink, notify_channel))

    def stop(self) -> None:
        """受信と文字起こしを停止する (冪等)"""
        if self._worker_task is not None:
            self._worker_task.cancel()
            self._worker_task = None

        if self._vc is not None:
            try:
                self._vc.stop_listening()
            except Exception:
                pass
            self._vc = None

    async def _worker(self, sink: UtteranceSink, notify_channel: Messageable | None) -> None:
        """0.3秒ごとに sink.pop_finished() を確認する常駐タスク"""
        min_bytes = self._config.MIN_SEC * BYTES_PER_SEC
        try:
            while True:
                await asyncio.sleep(0.3)
                finished = sink.pop_finished()
                # started 順にソート
                valid_utts = [utt for utt in finished if len(utt.pcm) >= min_bytes]
                valid_utts.sort(key=lambda u: u.started)

                for utt in valid_utts:
                    asyncio.create_task(self._handle(utt, notify_channel))
        except asyncio.CancelledError:
            pass

    async def _handle(self, utt: Utterance, notify_channel: Messageable | None) -> None:
        """1発話の文字起こし・キーワード検知・通知を行う"""
        if self._pending >= self._config.MAX_PENDING:
            logger.warning("Transcription backlog is full; dropping an utterance")
            return

        self._pending += 1
        try:
            loop = asyncio.get_running_loop()
            audio = pcm_to_16k_mono(utt.pcm)
            text = await loop.run_in_executor(self._executor, self._transcriber.transcribe, audio)
        except Exception:
            logger.exception("Transcription failed")
            return
        finally:
            self._pending -= 1

        if not text:
            return

        name = getattr(utt.member, "display_name", str(utt.member))
        ts = time.strftime("%H:%M:%S", time.localtime(utt.started))
        line = f"[{ts}] {name}: {text}"
        logger.info(line)

        # ログファイルへの追記
        if self._config.LOG_FILE:
            try:
                with open(self._config.LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                logger.exception("Failed to append to log file: %s", self._config.LOG_FILE)

        # キーワード検知
        found_keywords = self._detector.detect(text)
        if found_keywords:
            logger.info("Keywords found in %s's utterance: %s in %s", name, ", ".join(found_keywords), text)
            if notify_channel is not None:
                content = (
                    f"⚠️ キーワード検出: {utt.member.mention}\n"
                    f"検知したワード： `{', '.join(found_keywords)}`\n"
                    f"全文： `{text}`"
                )
                await self._notify(notify_channel, content)

    async def _notify(self, channel: Messageable | None, content: str) -> None:
        """通知メッセージを送信する (HTTPException はログに残して継続)"""
        if channel is None:
            return
        try:
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions.none(),
                silent=True,
            )
        except discord.HTTPException:
            logger.exception("Failed to send notification")
