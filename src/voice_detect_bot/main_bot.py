import os
import time
import asyncio
import logging
import threading
import numpy as np
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
import sherpa_onnx
from huggingface_hub import hf_hub_download

import discord
from discord import app_commands
from discord.ext import tasks, voice_recv
from discord.ext.voice_recv import VoiceRecvClient

load_dotenv()

SILENCE_SEC = 1.0   # この秒数パケットが来なければ「発話終了」とみなす
MIN_SEC = 0.5       # これより短い音声は捨てる
MAX_SEC = 30.0      # 長すぎる発話は強制的に区切る
BYTES_PER_SEC = 48000 * 2 * 2  # 48kHz / 16bit / ステレオ
MAX_PENDING = 30       # 文字起こし待ちがこれを超えたら、新しい発話は捨てる(遅延の暴走防止)
WATCHDOG_SEC = 30     # BOTの接続状態を揃え直す保険の間隔

MODEL_REPO = os.getenv("MODEL_REPO", "reazon-research/reazonspeech-k2-v2")
NUM_THREADS = int(os.getenv("ASR_THREADS", "2"))  # CPUスレッド数。1コア空けておくと音声受信が安定する

CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
NOTIFY_CHANNEL_ID = int(os.getenv('NOTIFY_CHANNEL_ID', 0))
NG_WORDS = os.getenv('NG_WORDS', '').split(',')
SILENT = os.getenv('SILENT', 'true').lower() == 'true'

auto_connect = os.getenv('AUTO_CONNECT', 'true').lower() == 'true'
session = None  # workerのタスク

# Loggerの設定
LOG_FORMAT = "%(asctime)s %(levelname)s     %(filename)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False

class _IgnoreRtcp(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "unexpected rtcp packet" not in record.getMessage() and "payload has extra" not in record.getMessage()
    
logging.getLogger("discord.ext.voice_recv.reader").addFilter(_IgnoreRtcp())
logging.getLogger("discord.ext.voice_recv.gateway").addFilter(_IgnoreRtcp())

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
logger.addHandler(handler)

logger.info(f"Loading model: {MODEL_REPO} (threads={NUM_THREADS})")

# マルチスレッドでモデルを呼び出すためのexecutorと、音声受信の排他制御用のLock
executor = ThreadPoolExecutor(max_workers=1)  # モデルへのアクセスは1スレッドに直列化
_voice_lock = asyncio.Lock()                # 音声受信の開始/停止は1スレッドに直列化
_worker_task: asyncio.Task | None = None    # 文字起こしworkerのタスク
_pending = 0                                # 文字起こし待ちの発話数

# ---------- 音声変換 / 文字起こし / キーワード ----------

def _load() -> sherpa_onnx.OfflineRecognizer:
    def dl(name: str) -> str:
        # 初回のみダウンロードされ、以降はキャッシュが使われる
        return hf_hub_download(repo_id=MODEL_REPO, filename=name)

    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=dl("encoder-epoch-99-avg-1.int8.onnx"),
        decoder=dl("decoder-epoch-99-avg-1.onnx"),
        joiner=dl("joiner-epoch-99-avg-1.onnx"),
        tokens=dl("tokens.txt"),
        num_threads=NUM_THREADS,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        provider="cpu",
    )

recognizer = _load()  # 文字起こしモデルのロードは時間がかかるので、最初に1回だけ行う

def pcm_to_16k_mono(pcm: bytes) -> np.ndarray:
    """48kHz/16bit/ステレオ PCM -> 16kHz/モノラル/float32"""
    pcm = pcm[: len(pcm) // 4 * 4]
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    a = a.reshape(-1, 2).mean(axis=1)
    n = len(a) // 3 * 3
    return a[:n].reshape(-1, 3).mean(axis=1)

 
def transcribe(audio: np.ndarray) -> str:
    """音声を文字起こしする。音声は16kHz/モノラル/float32であること"""
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, audio)
    recognizer.decode_stream(stream)
    return stream.result.text.strip()


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
        """ユーザーごとにPCMを貯める"""
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
        """すべてのバッファを破棄する"""
        with self._lock:
            self._buf.clear()
            self._started.clear()
            self._last.clear()
            self._members.clear()


# ---------- 文字起こしworker ----------
 
def detect_keywords(text: str) -> list[str]:
    """文字起こし結果からキーワードを検出する"""
    keywords = []
    for kw in NG_WORDS:
        if kw in text:
            keywords.append(kw)
    return keywords

async def safe_send(channel, content: str):
    """通知の失敗(権限不足など)で本体の処理が止まらないようにする"""
    if channel is None:
        return
    try:
        await channel.send(content, allowed_mentions=discord.AllowedMentions.none(),silent=SILENT)
    except discord.HTTPException:
        logger.exception("Failed to send notification")

async def handle_utterance(member, started: float, pcm: bytes):
    """1発話を文字起こしして、通知チャンネルに送る"""
    global _pending
    if _pending >= MAX_PENDING: # 文字起こし待ちが多すぎる場合は捨てる
        logger.warning("Transcription backlog is full; dropping an utterance")
        return
    _pending += 1
    try:
        # asyncioのイベントループとは別スレッドで文字起こしを行う
        loop = asyncio.get_running_loop()
        audio = pcm_to_16k_mono(pcm)
        text = await loop.run_in_executor(executor, transcribe, audio)
    except Exception:
        logger.exception("Transcription failed")
        return
    finally:
        _pending -= 1
 
    if not text:
        return
 
    name = getattr(member, "display_name", str(member))
    ts = time.strftime("%H:%M:%S", time.localtime(started))
    line = f"[{ts}] {name}: {text}"
    logger.info(line)

    # キーワードを検出
    keywords = detect_keywords(text)
    if keywords:
        logger.info(f"Keywords found in {name}'s utterance: {', '.join(keywords)} in {text}")
        if NOTIFY_CHANNEL_ID:
            channel = member.guild.get_channel(NOTIFY_CHANNEL_ID)
            if channel:
                await safe_send(channel, f"⚠️ キーワード検出: {member.mention}\n検知したワード： `{', '.join(keywords)}`\n全文： `{text}`")

async def worker(sink: UtteranceSink):
    """音声受信シンクから発話を取り出し、文字起こしする"""
    while True:
        await asyncio.sleep(0.3)
        for member, started, pcm in sink.pop_finished():
            if len(pcm) < MIN_SEC * BYTES_PER_SEC:
                continue
            asyncio.create_task(handle_utterance(member, started, pcm))


def start_worker(vc: VoiceRecvClient):
    """文字起こしworkerを開始する"""
    global _worker_task
    stop_worker()  # 古いworkerが残っていても二重にならないようにする
    sink = UtteranceSink()
    vc.listen(sink)
    _worker_task = asyncio.create_task(worker(sink))

def stop_worker():
    """文字起こしworkerを停止する"""
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        _worker_task = None


# ---------- 自動入退出 ----------
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
                    logger.info(f'Connecting to {channel}') # 接続されていない場合は接続する
                    vc = await channel.connect(self_mute=True, cls=VoiceRecvClient)
                    start_worker(vc)

                elif vc.channel.id != channel.id:
                    logger.info(f'Moving bot to {channel}') # 別のチャンネルにいる場合は移動する
                    await vc.move_to(channel)
            elif vc is not None:
                # 誰もいないなら、どこにいても切断する
                stop_worker()
                logger.info(f'Disconnecting from {vc.channel}')
                vc.stop_listening()
                await vc.disconnect()

        except (discord.ClientException, discord.Forbidden, asyncio.TimeoutError):
            logger.exception('Failed to sync voice state')

# ---------- Botのスピーカーミュート状態が変化したときの処理 ----------
async def on_bot_deaf_changed(guild, deafened: bool):
    """Botのスピーカーミュート状態が変化したときの処理"""
    if deafened:
        logger.warning(f'Bot was deafened in {guild}')
        stop_worker()
        # 通知、文字起こしの一時停止など
    else:
        logger.info(f'Bot was undeafened in {guild}')
        start_worker(guild.voice_client)
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

    @tasks.loop(seconds=WATCHDOG_SEC)
    async def voice_watchdog():
        for guild in client.guilds:
            await sync_voice(guild)
 
    @voice_watchdog.before_loop
    async def _before_watchdog():
        await client.wait_until_ready()
 
 
    @client.event
    async def on_ready():
        """Botが起動したときの処理"""
        # 起動時にすでに誰かがいる場合も、watchdogの初回実行で拾われる
        if not voice_watchdog.is_running():
            voice_watchdog.start()
        await tree.sync()
        logger.info(f'Bot is ready. Logged in as {client.user.name}')

    @client.event
    async def on_voice_state_update(member, before, after):
        """ボイスチャンネルに参加/退出したときの処理"""
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
        """Botの監視を開始する"""
        global auto_connect
        auto_connect = True
        for guild in client.guilds:
            await sync_voice(guild)
        await interaction.response.send_message("Botの監視を開始しました", ephemeral=True)


    @tree.command(name="stop_rec", description="Botの監視を停止します")
    @app_commands.default_permissions(administrator=True)
    async def stop_rec(interaction: discord.Interaction):
        """Botの監視を停止する"""
        global auto_connect
        auto_connect = False
        async with _voice_lock:
            for vc in list(client.voice_clients):
                stop_worker()
                await vc.disconnect()
        await interaction.response.send_message("Botの監視を停止しました", ephemeral=True)

    client.run(os.getenv('TOKEN'))

if __name__ == "__main__":
    main()