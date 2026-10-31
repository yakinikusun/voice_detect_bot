from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks
from discord.ext.voice_recv import VoiceRecvClient

from ..config import Config
from ..pipeline import TranscriptionPipeline

logger = logging.getLogger(__name__)


class VoiceManager:
    """自動入退出と bot の音声接続状態を管理する"""

    def __init__(self, bot: discord.Client, config: Config, pipeline: TranscriptionPipeline):
        self._bot = bot
        self._config = config
        self._pipeline = pipeline

        self._voice_lock = asyncio.Lock()
        self.auto_join_enabled: bool = True
        self._watchdog_task: tasks.Loop | None = None

    async def sync_voice(self, guild: discord.Guild) -> None:
        """対象チャンネルの人間の有無に合わせて、botの接続状態を揃える (冪等)"""
        channel = guild.get_channel(self._config.VOICE_CHANNEL_ID)
        if channel is None or not hasattr(channel, "connect"):
            return

        async with self._voice_lock:
            if not self.auto_join_enabled:
                return

            has_humans = any(not m.bot for m in channel.members)
            vc = guild.voice_client

            if vc is None:
                # 残骸整理
                self._pipeline.stop()

            try:
                if has_humans:
                    notify_channel = guild.get_channel(self._config.NOTIFY_CHANNEL_ID)
                    if vc is None:
                        logger.info("Connecting to %s in %s", getattr(channel, "name", channel), guild.name)
                        new_vc = await channel.connect(self_mute=True, cls=VoiceRecvClient)
                        self._pipeline.start(new_vc, notify_channel)
                        # 入室の告知
                        if notify_channel is not None and isinstance(notify_channel, discord.abc.Messageable):
                            try:
                                await notify_channel.send(
                                    f"🔊 `{getattr(channel, 'name', channel)}` に接続し、音声の文字起こしを開始しました。",
                                    silent=True,
                                    allowed_mentions=discord.AllowedMentions.none(),
                                )
                            except discord.HTTPException:
                                pass
                    elif vc.channel.id != channel.id:
                        logger.info("Moving bot from %s to %s", vc.channel.name, getattr(channel, "name", channel))
                        await vc.move_to(channel)
                elif vc is not None:
                    # 人間がいないならどこにいても切断
                    logger.info("Disconnecting from %s in %s (no humans)", vc.channel.name, guild.name)
                    self._pipeline.stop()
                    await vc.disconnect()
            except (discord.ClientException, discord.Forbidden, asyncio.TimeoutError):
                logger.exception("Failed to sync voice state in guild %s", guild.name)

    async def set_auto_join(self, enabled: bool) -> None:
        """自動入退出の ON/OFF を切り替える"""
        if self.auto_join_enabled == enabled:
            return

        self.auto_join_enabled = enabled
        if not enabled:
            async with self._voice_lock:
                self._pipeline.stop()
                for vc in list(self._bot.voice_clients):
                    try:
                        await vc.disconnect()
                    except Exception:
                        logger.exception("Failed to disconnect vc during set_auto_join(False)")
        else:
            # sync_voice は内部でロックを取るため、ロックの外から呼ぶ
            for guild in self._bot.guilds:
                await self.sync_voice(guild)

    def start_watchdog(self) -> None:
        """watchdog_sec ごとに全ギルドへ sync_voice を呼ぶ保険のループを開始する"""
        if self._watchdog_task is not None and self._watchdog_task.is_running():
            return

        @tasks.loop(seconds=self._config.WATCHDOG_SEC)
        async def watchdog_loop():
            for guild in self._bot.guilds:
                await self.sync_voice(guild)

        @watchdog_loop.before_loop
        async def before_watchdog():
            await self._bot.wait_until_ready()

        self._watchdog_task = watchdog_loop
        watchdog_loop.start()

    async def handle_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """on_voice_state_update から委譲されるハンドラ"""
        # (1) bot 自身の deaf が変化したら通知
        if self._bot.user is not None and member.id == self._bot.user.id and before.deaf != after.deaf:
            await self._on_bot_deaf_changed(member.guild, after.deaf)

        # (2) before.channel == after.channel なら終了
        if before.channel == after.channel:
            return

        # (3) 対象チャンネルに無関係なら終了
        touched = {c.id for c in (before.channel, after.channel) if c is not None}
        if self._config.VOICE_CHANNEL_ID not in touched:
            return

        # (4) bot 自身のイベントなら 2秒待つ (discord.py の状態反映待ち)
        if self._bot.user is not None and member.id == self._bot.user.id:
            await asyncio.sleep(2)

        # (5) sync_voice
        await self.sync_voice(member.guild)

    async def _on_bot_deaf_changed(self, guild: discord.Guild, deafened: bool) -> None:
        """スピーカーミュートされた/解除されたときの処理"""
        state_str = "スピーカーミュート (Deafened)" if deafened else "スピーカーミュート解除 (Undeafened)"
        logger.warning("Bot deaf state changed in %s: %s", guild.name, state_str)

        notify_channel = guild.get_channel(self._config.NOTIFY_CHANNEL_ID)
        if notify_channel is not None and isinstance(notify_channel, discord.abc.Messageable):
            msg = "🔇 Botがスピーカーミュートされました。文字起こしを一時停止します。" if deafened else "🔊 Botのスピーカーミュートが解除されました。文字起こしを再開します。"
            try:
                await notify_channel.send(msg, silent=True, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                logger.exception("Failed to send deaf change notification")

        if deafened:
            self._pipeline.stop()
        else:
            vc = guild.voice_client
            if vc is not None:
                self._pipeline.start(vc, notify_channel)

