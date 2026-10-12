import os
import time
import asyncio
import logging
import threading
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import tasks, voice_recv
from discord.ext.voice_recv import VoiceRecvClient


load_dotenv()

# Loggerの設定
LOG_FORMAT = "%(asctime)s %(levelname)s     %(filename)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

SILENCE_SEC = 1.0   # この秒数パケットが来なければ「発話終了」とみなす
MIN_SEC = 0.5       # これより短い音声は捨てる
MAX_SEC = 30.0      # 長すぎる発話は強制的に区切る
 
BYTES_PER_SEC = 48000 * 2 * 2  # 48kHz / 16bit / ステレオ

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False

class _IgnoreRtcp(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "unexpected rtcp packet" not in record.getMessage()
    
logging.getLogger("discord.ext.voice_recv.reader").addFilter(_IgnoreRtcp())

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
logger.addHandler(handler)

auto_connect = os.getenv('AUTO_CONNECT', 'true').lower() == 'true'
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
NOTIFY_CHANNEL_ID = int(os.getenv('NOTIFY_CHANNEL_ID', 0))
session = None  # workerのタスク

# connect / move / disconnect を直列化する(同時に複数のconnectが走るのを防ぐ)
_voice_lock = asyncio.Lock()

# ---------- 音声受信シンク ----------
class UtteranceSink(voice_recv.AudioSink):
    """ユーザーごとにPCMを貯め、無音が続いたら「1発話」として取り出せるようにする。
 
    write() はイベントループとは別スレッドから呼ばれるため、ロックで保護している。
    """
 
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._buf: dict[int, bytearray] = {}
        self._started: dict[int, float] = {}
        self._last: dict[int, float] = {}
        self._members: dict[int, discord.abc.User] = {}
 
    def wants_opus(self) -> bool:
        return False  # デコード済みPCMで受け取る
 
    def write(self, user, data: voice_recv.VoiceData):
        if user is None or user.bot or not data.pcm:
            return
        with self._lock:
            if user.id not in self._buf:
                self._buf[user.id] = bytearray()
                self._started[user.id] = time.time()
            self._buf[user.id].extend(data.pcm)
            self._last[user.id] = time.monotonic()
            self._members[user.id] = user
 
    def pop_finished(self):
        """発話が終わった(または長すぎる)ユーザーの音声を取り出す"""
        now = time.monotonic()
        out = []
        with self._lock:
            for uid in list(self._buf):
                idle = now - self._last[uid]
                too_long = len(self._buf[uid]) >= MAX_SEC * BYTES_PER_SEC
                if idle >= SILENCE_SEC or too_long:
                    out.append(
                        (self._members[uid], self._started.pop(uid), bytes(self._buf.pop(uid)))
                    )
                    self._last.pop(uid, None)
        return out
 
    def cleanup(self):
        with self._lock:
            self._buf.clear()
            self._started.clear()
            self._last.clear()
            self._members.clear()

async def worker(sink: UtteranceSink):
    while True:
        await asyncio.sleep(0.3)
        for member, started, pcm in sink.pop_finished():
            if len(pcm) < MIN_SEC * BYTES_PER_SEC:
                continue
            logger.info(f'Utterance finished: {member} ({len(pcm)} bytes)')

# ---------- Botの接続状態を人間の有無に合わせて揃える ----------
async def sync_voice(guild: discord.Guild):
    """対象チャンネルに人間がいるかどうかに合わせて、botの接続状態を揃える。
    何度呼んでも安全(冪等)。"""
    channel = guild.get_channel(CHANNEL_ID)
    if channel is None:
        return
 
    async with _voice_lock:
        if not auto_connect: # 自動接続が無効な場合は、botが接続されているかどうかに関わらず何もしない
            return
        # ロックを取ってから状態を読む(待っている間に状況が変わるため)
        has_humans = any(not m.bot for m in channel.members)
        vc = guild.voice_client
        global session
 
        try:
            if has_humans: 
                # 誰かがいる場合は、botを接続する
                if vc is None:
                    if session:
                        session.cancel()
                        session = None
                    logger.info(f'Connecting to {channel}') # 接続されていない場合は接続する
                    vc = await channel.connect(self_mute=True, cls=VoiceRecvClient)
                    sink = UtteranceSink()
                    vc.listen(sink)
                    session = asyncio.create_task(worker(sink))

                elif vc.channel.id != channel.id:
                    if session:
                        session.cancel()
                        session = None
                    logger.info(f'Moving bot to {channel}') # 別のチャンネルにいる場合は移動する
                    await vc.move_to(channel)
            elif vc is not None:
                # 誰もいないなら、どこにいても切断する
                logger.info(f'Disconnecting from {vc.channel}')
                if session:
                    session.cancel()
                    session = None
                await vc.disconnect()

        except (discord.ClientException, discord.Forbidden, asyncio.TimeoutError):
            logger.exception('Failed to sync voice state')

# ---------- Botのスピーカーミュート状態が変化したときの処理 ----------
async def on_bot_deaf_changed(guild, deafened: bool):
    if deafened:
        logger.warning(f'Bot was deafened in {guild}')
        # 通知、文字起こしの一時停止など
    else:
        logger.info(f'Bot was undeafened in {guild}')
        # 文字起こしの再開など

# ---------- Botのメイン処理 ----------
def main():

    # Discord BotのIntentsを設定
    intents = discord.Intents.default()

    intents.expressions = True
    intents.members = True
    intents.message_content = True
    intents.voice_states = True

    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

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
        await tree.sync()
        logger.info(f'Bot is ready. Logged in as {client.user.name}')

    # ボイスチャンネルに参加/退出したときの処理
    @client.event
    async def on_voice_state_update(member, before, after):
        logger.debug(f'Voice state updated for {member.name}: {before.channel} -> {after.channel}')

        # Botのスピーカーミュート状態が変化した場合の処理
        if member.id == client.user.id and before.deaf != after.deaf:
            await on_bot_deaf_changed(member.guild, after.deaf)

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

    @tree.command(name="start_rec", description="Botの監視を開始します")
    @app_commands.default_permissions(administrator=True)
    async def start_rec(interaction: discord.Interaction):
        global auto_connect
        auto_connect = True
        for guild in client.guilds:
            await sync_voice(guild)
        await interaction.response.send_message("Botの監視を開始しました", ephemeral=True)


    @tree.command(name="stop_rec", description="Botの監視を停止します")
    @app_commands.default_permissions(administrator=True)
    async def stop_rec(interaction: discord.Interaction):
        global auto_connect
        auto_connect = False
        async with _voice_lock:
            for vc in list(client.voice_clients):
                if session:
                    session.cancel()
                    session = None
                await vc.disconnect()
        await interaction.response.send_message("Botの監視を停止しました", ephemeral=True)

    client.run(os.getenv('TOKEN'))

if __name__ == "__main__":
    main()