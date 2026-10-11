import os
import discord
from discord.ext.voice_recv import VoiceRecvClient
from dotenv import load_dotenv

load_dotenv()

voice_channel = None
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

def main():

    # Discord BotのIntentsを設定
    intents = discord.Intents.default()

    intents.expressions = True
    intents.members = True
    intents.message_content = True
    intents.voice_states = True

    client = discord.Client(intents=intents)

    # Bot起動時の処理
    @client.event
    async def on_ready():
        print(f'We have logged in as {client.user.name}')

    # ボイスチャンネルに参加/退出したときの処理
    @client.event
    async def on_voice_state_update(member, before, after):
        global voice_channel

        # print(f'Voice state updated for {member.name}: {before.channel} -> {after.channel}')

        # 対象チャンネル以外のイベントは無視する
        if (before.channel is not None and before.channel.id != CHANNEL_ID) and (after.channel is not None and after.channel.id != CHANNEL_ID):
            return

        # 同じチャンネル内でのイベントとBotの入退出は無視する
        if before.channel == after.channel or member.bot:
            return

        # 対象チャンネルに参加した場合の処理
        if after.channel is not None and after.channel.id == CHANNEL_ID:
            print(f'{member.name} joined the voice channel: {after.channel}')
            
            # すでに接続している場合は何もしない
            if after.channel.guild.voice_client is None:
                voice_channel = after.channel
                await after.channel.connect(cls=VoiceRecvClient)
            return
        
        # 対象チャンネルから退出した場合の処理
        elif before.channel is not None and before.channel.id == CHANNEL_ID:
            print(f'{member.name} left the voice channel: {before.channel}')

            # Bot以外のユーザーがまだチャンネルに残っている場合は何もしない
            if any(not channel_member.bot for channel_member in before.channel.members):
                return
            
            voice_client = before.channel.guild.voice_client
            if voice_client is not None:
                await voice_client.disconnect()
            voice_channel = None

    client.run(os.getenv('TOKEN'))

if __name__ == "__main__":
    main()