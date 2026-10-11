import os
import asyncio
import logging
from dotenv import load_dotenv

import discord
from discord.ext import tasks
from discord.ext.voice_recv import VoiceRecvClient

load_dotenv()

# Loggerの設定
LOG_FORMAT = "%(asctime)s %(levelname)s     %(filename)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
logger.addHandler(handler)

voice_channel = None
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

# connect / move / disconnect を直列化する(同時に複数のconnectが走るのを防ぐ)
_voice_lock = asyncio.Lock()
 
 
async def sync_voice(guild: discord.Guild):
    """対象チャンネルに人間がいるかどうかに合わせて、botの接続状態を揃える。
    何度呼んでも安全(冪等)。"""
    channel = guild.get_channel(CHANNEL_ID)
    if channel is None:
        return
 
    async with _voice_lock:
        # ロックを取ってから状態を読む(待っている間に状況が変わるため)
        has_humans = any(not m.bot for m in channel.members)
        vc = guild.voice_client
 
        try:
            if has_humans:
                if vc is None:
                    logger.info(f'Connecting to {channel}')
                    vc = await channel.connect(cls=VoiceRecvClient)
                    # ここで vc.listen(sink) や worker の起動を行う
                elif vc.channel.id != channel.id:
                    logger.info(f'Moving bot to {channel}')
                    await vc.move_to(channel)
            elif vc is not None:
                # 誰もいないなら、どこにいても切断する
                logger.info(f'Disconnecting from {vc.channel}')
                await vc.disconnect()

        except (discord.ClientException, discord.Forbidden, asyncio.TimeoutError):
            logger.exception('Failed to sync voice state')


def main():

    # Discord BotのIntentsを設定
    intents = discord.Intents.default()

    intents.expressions = True
    intents.members = True
    intents.message_content = True
    intents.voice_states = True

    client = discord.Client(intents=intents)

    @tasks.loop(seconds=10)
    async def voice_watchdog():
        for guild in client.guilds:
            await sync_voice(guild)
 
 
    @voice_watchdog.before_loop
    async def _before_watchdog():
        await client.wait_until_ready()
 
 
    @client.event
    async def on_ready():
        # 起動時にすでに誰かがいる場合も、watchdogの初回実行で拾われる
        if not voice_watchdog.is_running():
            voice_watchdog.start()

    # Bot起動時の処理
    @client.event
    async def on_ready():
        for guild in client.guilds:
            await sync_voice(guild)
        logger.info(f'Bot is ready. Logged in as {client.user.name}')

    # ボイスチャンネルに参加/退出したときの処理
    @client.event
    async def on_voice_state_update(member, before, after):
        logger.debug(f'Voice state updated for {member.name}: {before.channel} -> {after.channel}')

        # 同じチャンネル内の変化(ミュート切替など)は無視
        if before.channel == after.channel:
            return
 
        # 対象チャンネルに関係ない移動は無視(botが対象外へ移動された場合はwatchdogが拾う)
        touched = {c.id for c in (before.channel, after.channel) if c is not None}
        if CHANNEL_ID not in touched:
            return
 
        if member.id == client.user.id:
            await asyncio.sleep(2) # 動きが早くしすぎないように、botの移動は少し待つ
 
        await sync_voice(member.guild)

    client.run(os.getenv('TOKEN'))

if __name__ == "__main__":
    main()