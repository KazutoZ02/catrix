#!/usr/bin/env python3
import os, json, time, asyncio, logging
from typing import Optional, Dict
from dataclasses import dataclass, field

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import httpx

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# =====================================================
# ENV + LOGGING
# =====================================================
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CatTrix")

# =====================================================
# CONFIG
# =====================================================
@dataclass
class Config:
    discord_token: str = os.getenv("DISCORD_TOKEN")
    poll_interval: float = float(os.getenv("POLL_INTERVAL", 2))
    ai_cooldown: int = int(os.getenv("AI_COOLDOWN", 15))
    max_message_length: int = int(os.getenv("MAX_MESSAGE_LENGTH", 140))
    scopes: list = field(default_factory=lambda: os.getenv(
        "YOUTUBE_SCOPES",
        "https://www.googleapis.com/auth/youtube.force-ssl"
    ).split(","))

# =====================================================
# AI PRESETS
# =====================================================
AI_PERSONALITIES = {
    "cattrix": {
        "system": "You are CatTrix — playful, witty, chaotic but kind. Short Hinglish replies.",
        "temp": 0.8,
        "tokens": 80
    },
    "calm": {
        "system": "You are calm, friendly, supportive.",
        "temp": 0.6,
        "tokens": 100
    },
    "roast": {
        "system": "You roast lightly, never insult.",
        "temp": 0.9,
        "tokens": 60
    }
}

# =====================================================
# AI SERVICE (LOW LATENCY)
# =====================================================
class AIService:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last_used = 0
        self.active_personality = "cattrix"
        self.client = httpx.AsyncClient(timeout=20)
        self.key = os.getenv("OPENROUTER_API_KEY")
        self.model = os.getenv("OPENROUTER_MODEL")

    async def reply(self, message: str, author: str) -> Optional[str]:
        if time.time() - self.last_used < self.cfg.ai_cooldown:
            return None

        preset = AI_PERSONALITIES[self.active_personality]

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": preset["system"]},
                {"role": "user", "content": f"{author}: {message}"}
            ],
            "temperature": preset["temp"],
            "max_tokens": preset["tokens"]
        }

        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json"
        }

        r = await self.client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers
        )

        if r.status_code != 200:
            return None

        self.last_used = time.time()
        text = r.json()["choices"][0]["message"]["content"]
        return text[:self.cfg.max_message_length]

# =====================================================
# YOUTUBE OAUTH SERVICE
# =====================================================
class YouTubeOAuth:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.creds: Optional[Credentials] = None
        self.youtube = None

    def login(self):
        token_file = "token.json"

        if os.path.exists(token_file):
            self.creds = Credentials.from_authorized_user_file(
                token_file, self.cfg.scopes
            )

        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    os.getenv("GOOGLE_CLIENT_SECRET_FILE"),
                    self.cfg.scopes
                )
                self.creds = flow.run_local_server(port=0)

            with open(token_file, "w") as f:
                f.write(self.creds.to_json())

        self.youtube = build("youtube", "v3", credentials=self.creds)
        logger.info("YouTube account authenticated")

# =====================================================
# MODERATION COMMANDS (SLASH)
# =====================================================
class Moderation(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="ban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction, member: discord.Member, reason: str = None):
        await member.ban(reason=reason)
        await interaction.response.send_message(f"🔨 Banned {member}")

    @app_commands.command(name="kick")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction, member: discord.Member, reason: str = None):
        await member.kick(reason=reason)
        await interaction.response.send_message(f"👢 Kicked {member}")

    @app_commands.command(name="timeout")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, interaction, member: discord.Member, minutes: int):
        await member.timeout(discord.utils.utcnow() + discord.timedelta(minutes=minutes))
        await interaction.response.send_message(f"⏳ Timed out {member}")

    @app_commands.command(name="purge")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction, amount: int):
        await interaction.channel.purge(limit=amount)
        await interaction.response.send_message("🧹 Messages deleted", ephemeral=True)

# =====================================================
# BOT
# =====================================================
class CatTrixBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

        self.cfg = Config()
        self.ai = AIService(self.cfg)
        self.yt = YouTubeOAuth(self.cfg)

    async def setup_hook(self):
        await self.add_cog(Moderation(self))
        await self.tree.sync()

    async def on_ready(self):
        logger.info("🐱 CatTrix online as %s", self.user)

# =====================================================
# MAIN
# =====================================================
async def main():
    bot = CatTrixBot()
    await bot.start(bot.cfg.discord_token)

if __name__ == "__main__":
    asyncio.run(main())