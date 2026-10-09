import os
import discord
from dotenv import load_dotenv


load_dotenv()

def main():
    intents = discord.Intents.none()

    intents.expressions = True
    intents.members = True
    intents.message_content = True
    intents.voice_states = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f'We have logged in as {client.user.name}')

    client.run(os.getenv('TOKEN'))

if __name__ == "__main__":
    main()