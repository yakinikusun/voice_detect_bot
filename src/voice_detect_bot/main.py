from __future__ import annotations

import logging
import sys

import discord
from discord import app_commands

from .config import Config, load_config
from .keywords import KeywordDetector
from .pipeline import TranscriptionPipeline
from .stt import Transcriber, create_transcriber
from .voice.autojoin import VoiceManager

logger = logging.getLogger(__name__)


class _IgnoreRtcp(logging.Filter):
    """voice-recv の RTCP パケット警告ノイズを除去するフィルタ"""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "unexpected rtcp packet" not in msg and "payload has extra" not in msg


def setup_logging() -> None:
    """ロギングの設定

    discord.py 等の外部ライブラリの大量の INFO ログが流れないよう、
    本パッケージ (voice_detect_bot) 専用のロガーにハンドラを設定し、
    上位への伝播 (propagate) を無効化する。
    """
    log_format = "%(asctime)s %(levelname)s     %(filename)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    app_logger = logging.getLogger("voice_detect_bot")
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    app_logger.addHandler(handler)

    # RTCP パケットノイズ除去フィルタ
    logging.getLogger("discord.ext.voice_recv.reader").addFilter(_IgnoreRtcp())
    logging.getLogger("discord.ext.voice_recv.gateway").addFilter(_IgnoreRtcp())



def build_bot(config: Config, transcriber: Transcriber) -> discord.Client:
    """Bot を組み立てる (副作用なし、起動はしない)"""
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.voice_states = True

    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    detector = KeywordDetector(config.KEYWORDS)
    pipeline = TranscriptionPipeline(config, transcriber, detector)
    voice_manager = VoiceManager(client, config, pipeline)

    @client.event
    async def on_ready():
        voice_manager.start_watchdog()
        try:
            await tree.sync()
            logger.info("Application command tree synced")
        except Exception:
            logger.exception("Failed to sync application command tree")
        if client.user:
            logger.info("Bot is ready. Logged in as %s", client.user.name)

    @client.event
    async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        await voice_manager.handle_voice_state_update(member, before, after)

    # --- スラッシュコマンド ---
    @tree.command(name="rec_toggle", description="自動入退出および音声監視の有効/無効を切り替えます")
    @app_commands.describe(enabled="有効にするか、無効にして切断・監視停止するか")
    @app_commands.choices(
        enabled=[
            app_commands.Choice(name="有効 (監視を開始し、人がいる時に自動接続)", value="on"),
            app_commands.Choice(name="無効 (監視を停止し、VCから切断)", value="off"),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def rec_toggle_command(interaction: discord.Interaction, enabled: app_commands.Choice[str]):
        """自動入退出と監視を一括で切り替えるコマンド"""
        if enabled.value == "on":
            await voice_manager.set_auto_join(True)
            await interaction.response.send_message(
                "🔊 自動入退出と音声監視を有効化しました。（人がいる場合に自動接続します）",
                ephemeral=True,
            )
        else:
            await voice_manager.set_auto_join(False)
            await interaction.response.send_message(
                "🔇 自動入退出と音声監視を無効化し、ボイスチャンネルから切断・監視を停止しました。",
                ephemeral=True,
            )

    return client


def main() -> None:
    """エントリポイント"""
    config = load_config()
    setup_logging()

    logger.info("Initializing STT backend (%s)...", config.STT_BACKEND)
    transcriber = create_transcriber(config)

    logger.info("Building Discord bot...")
    bot = build_bot(config, transcriber)

    logger.info("Starting Discord bot...")
    bot.run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
