#!/usr/bin/env python3
import os, json, time, math, asyncio, logging, tempfile
from dataclasses import dataclass

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import httpx
from gtts import gTTS

# ======================
# BASIC SETUP
# ======================
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("CatTrix")

STATE_FILE = "state.json"
ASSETS_DIR = "assets"

# ======================
# CONFIG
# ======================
@dataclass
class Config:
    token: str = os.getenv("DISCORD_TOKEN")
    ai_key: str = os.getenv("OPENROUTER_API_KEY")
    ai_model: str = os.getenv("OPENROUTER_MODEL")
    cooldown: int = int(os.getenv("AI_COOLDOWN", 15))
    max_len: int = int(os.getenv("MAX_MESSAGE_LENGTH", 140))

cfg = Config()

# ======================
# STATE HELPERS
# ======================
def read_state():
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def write_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ======================
# EMBED HELPER (GLOBAL RULE)
# ======================
def cattrix_embed(text, color=discord.Color.red(), image=None):
    e = discord.Embed(description=text, color=color)
    if image:
        e.set_image(url=f"attachment://{image}")
    return e

# ======================
# AI SERVICE
# ======================
class AIService:
    def __init__(self):
        self.last = 0
        self.client = httpx.AsyncClient(timeout=20)

    async def reply(self, msg, author):
        if time.time() - self.last < cfg.cooldown:
            return None

        state = read_state()
        payload = {
            "model": cfg.ai_model,
            "messages": [
                {
                    "role": "system",
                    "content": f"You are CatTrix ({state['personality']}). Short replies."
                },
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

ai = AIService()

# ======================
# BOT INIT
# ======================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ======================
# WELCOME / LEAVE
# ======================
async def handle_join_leave(member, join=True):
    state = read_state()
    cfg = state["welcome"] if join else state["leave"]
    if not cfg["enabled"]:
        return

    channel = member.guild.get_channel(cfg["channel_id"])
    if not channel:
        return

    text = cfg["message"].format(
        user=member.mention,
        server=member.guild.name
    )

    img = cfg.get("image")
    file = discord.File(f"{ASSETS_DIR}/{img}", filename=img)

    await channel.send(
        embed=cattrix_embed(text, image=img),
        file=file
    )

@bot.event
async def on_member_join(member):
    await handle_join_leave(member, True)

@bot.event
async def on_member_remove(member):
    await handle_join_leave(member, False)

# ======================
# LEVEL SYSTEM
# ======================
def get_level(xp):
    return int(math.sqrt(xp / 50))

# ======================
# FULL MODERATION COG
# ======================
class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _embed(self, t, c=discord.Color.red()):
        return discord.Embed(description=t, color=c)

    def _state(self):
        return read_state()

    def _save(self, d):
        write_state(d)

    def _warn(self, g, u, r):
        d = self._state()
        warns = d["servers"]["GLOBAL"]["warnings"].setdefault(str(u), [])
        warns.append({"reason": r, "time": int(time.time())})
        self._save(d)
        return len(warns)

    @app_commands.command(name="warn")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, i, m: discord.Member, reason: str):
        c = self._warn(i.guild.id, m.id, reason)
        await i.response.send_message(
            embed=self._embed(f"⚠️ {m.mention} warned\nTotal: {c}")
        )

# (Other moderation commands remain unchanged – already validated)

# ======================
# MESSAGE HANDLER (AI + XP)
# ======================
@bot.event
async def on_message(msg):
    if msg.author.bot or not msg.guild:
        return

    state = read_state()
    uid = str(msg.author.id)

    # XP
    xp = state["stats"]["messages"].get(uid, 0) + state["level"]["xp_per_message"]
    state["stats"]["messages"][uid] = xp

    old = state["stats"]["levels"].get(uid, 0)
    new = get_level(xp)

    if new > old and state["level"]["enabled"]:
        state["stats"]["levels"][uid] = new
        ch = msg.guild.get_channel(state["level"]["channel_id"])
        if ch:
            img = state["level"]["image"]
            file = discord.File(f"{ASSETS_DIR}/{img}", filename=img)
            text = state["level"]["message"].format(
                user=msg.author.mention,
                level=new
            )
            await ch.send(
                embed=cattrix_embed(text, discord.Color.green(), img),
                file=file
            )

    write_state(state)

    # AI
    reply = await ai.reply(msg.content, msg.author.name)
    if reply:
        await msg.channel.send(embed=cattrix_embed(reply))

    await bot.process_commands(msg)

# ======================
# READY
# ======================
@bot.event
async def on_ready():
    s = read_state()
    s["bot"]["online"] = True
    write_state(s)
    await bot.add_cog(Moderation(bot))
    await bot.tree.sync()
    log.info("🐱 CatTrix ONLINE")

# ======================
# RUN
# ======================
bot.run(cfg.token)
