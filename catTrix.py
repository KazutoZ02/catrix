#!/usr/bin/env python3
import os, json, time, asyncio, logging
from typing import Optional
from dataclasses import dataclass

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import httpx

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from gtts import gTTS
import tempfile

# ======================
# SETUP
# ======================
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("CatTrix")

STATE_FILE = "state.json"

# ======================
# CONFIG
# ======================
@dataclass
class Config:
    token: str = os.getenv("DISCORD_TOKEN")
    ai_key: str = os.getenv("OPENROUTER_API_KEY")
    ai_model: str = os.getenv("OPENROUTER_MODEL")
    yt_key: str = os.getenv("YOUTUBE_API_KEY")
    poll: int = int(os.getenv("POLL_INTERVAL", 5))
    cooldown: int = int(os.getenv("AI_COOLDOWN", 15))
    max_len: int = int(os.getenv("MAX_MESSAGE_LENGTH", 140))

cfg = Config()

# ======================
# STATE
# ======================
def read_state():
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def write_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ======================
# AI SERVICE
# ======================
class AI:
    def __init__(self):
        self.last = 0
        self.client = httpx.AsyncClient(timeout=20)

    async def reply(self, msg, author):
        if time.time() - self.last < cfg.cooldown:
            return None

        state = read_state()
        personality = state["personality"]

        payload = {
            "model": cfg.ai_model,
            "messages": [
                {"role": "system", "content": f"You are CatTrix ({personality}). Short replies."},
                {"role": "user", "content": f"{author}: {msg}"}
            ],
            "max_tokens": 80
        }

        r = await self.client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {cfg.ai_key}"},
            json=payload
        )

        if r.status_code != 200:
            return None

        self.last = time.time()
        return r.json()["choices"][0]["message"]["content"][:cfg.max_len]

ai = AI()

# ======================
# YOUTUBE SERVICE
# ======================
yt = build("youtube", "v3", developerKey=cfg.yt_key)

# ======================
# BOT
# ======================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

def embed(msg, color=discord.Color.red()):
    return discord.Embed(description=msg, color=color)

# ======================
# MODERATION CMDS
# ======================
class Moderation(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="ban")
    async def ban(self, i: discord.Interaction, m: discord.Member, reason: str = None):
        await m.ban(reason=reason)
        await i.response.send_message(embed=embed(f"🔨 Banned {m}"))

    @app_commands.command(name="kick")
    async def kick(self, i, m: discord.Member, reason: str = None):
        await m.kick(reason=reason)
        await i.response.send_message(embed=embed(f"👢 Kicked {m}"))

    @app_commands.command(name="timeout")
    async def timeout(self, i, m: discord.Member, minutes: int):
        await m.timeout(discord.utils.utcnow() + discord.timedelta(minutes=minutes))
        await i.response.send_message(embed=embed(f"⏳ Timed out {m}"))

    @app_commands.command(name="purge")
    async def purge(self, i, amount: int):
        await i.channel.purge(limit=amount)
        await i.response.send_message(embed=embed("🧹 Messages deleted"), ephemeral=True)

# ======================
# TTS
# ======================
async def tts_play(vc: discord.VoiceClient, text: str):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
        gTTS(text).save(f.name)
        vc.play(discord.FFmpegPCMAudio(f.name))

# ======================
# EVENTS
# ======================
@bot.event
async def on_ready():
    state = read_state()
    state["bot"]["online"] = True
    write_state(state)
    await bot.add_cog(Moderation(bot))
    await bot.tree.sync()
    log.info("🐱 CatTrix ONLINE")

# ======================
# DISCORD AI REPLY (EMBED)
# ======================
@bot.event
async def on_message(msg):
    if msg.author.bot:
        return

    reply = await ai.reply(msg.content, msg.author.name)
    if reply:
        await msg.channel.send(embed=embed(reply))

    await bot.process_commands(msg)

# ======================
# RUN
# ======================
bot.run(cfg.token)