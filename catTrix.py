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
# WELCOME & LEAVE
# ======================
async def handle_welcome_leave(member: discord.Member, join=True):
    state = read_state()
    cfg = state["welcome"] if join else state["leave"]
    if not cfg["enabled"]:
        return

    channel = member.guild.get_channel(cfg["channel_id"])
    if not channel:
        return

    msg = cfg["message"].format(
        user=member.mention,
        server=member.guild.name
    )

    image = cfg.get("image")
    file = discord.File(f"{ASSETS_DIR}/{image}", filename=image) if image else None

    await channel.send(
        embed=cattrix_embed(msg, image=image),
        file=file
    )

@bot.event
async def on_member_join(member):
    await handle_welcome_leave(member, join=True)

@bot.event
async def on_member_remove(member):
    await handle_welcome_leave(member, join=False)


# ======================
# LEVEL UP EXP
# ======================
def get_level(xp):
    return int(math.sqrt(xp / 50))

@bot.event
async def on_message(msg):
    if msg.author.bot:
        return

    state = read_state()
    uid = str(msg.author.id)
    gid = str(msg.guild.id)

    stats = state["stats"].setdefault("messages", {})
    stats[uid] = stats.get(uid, 0) + state["level"]["xp_per_message"]

    old_lvl = state["stats"]["levels"].get(uid, 0)
    new_lvl = get_level(stats[uid])

    if new_lvl > old_lvl and state["level"]["enabled"]:
        state["stats"]["levels"][uid] = new_lvl
        write_state(state)

        cfg = state["level"]
        ch = msg.guild.get_channel(cfg["channel_id"])
        if ch:
            img = cfg["image"]
            file = discord.File(f"{ASSETS_DIR}/{img}", filename=img)
            text = cfg["message"].format(user=msg.author.mention, level=new_lvl)

            await ch.send(
                embed=cattrix_embed(text, discord.Color.green(), img),
                file=file
            )

    write_state(state)
    await bot.process_commands(msg)
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
# FULL MODERATION COG
# ======================
class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------- HELPERS ----------
    def _embed(self, text, color=discord.Color.red()):
        return discord.Embed(description=text, color=color)

    def _load_state(self):
        with open("state.json") as f:
            return json.load(f)

    def _save_state(self, data):
        with open("state.json", "w") as f:
            json.dump(data, f, indent=2)

    def _add_warning(self, guild_id, user_id, reason):
        data = self._load_state()
        g = data.setdefault("servers", {}).setdefault(str(guild_id), {})
        warns = g.setdefault("warnings", {}).setdefault(str(user_id), [])
        warns.append({
            "reason": reason,
            "time": int(time.time())
        })
        self._save_state(data)
        return len(warns)

    def _get_warnings(self, guild_id, user_id):
        data = self._load_state()
        return data.get("servers", {}).get(str(guild_id), {}) \
                   .get("warnings", {}).get(str(user_id), [])

    def _clear_warnings(self, guild_id, user_id):
        data = self._load_state()
        try:
            del data["servers"][str(guild_id)]["warnings"][str(user_id)]
            self._save_state(data)
            return True
        except KeyError:
            return False

    # ---------- COMMANDS ----------

    @app_commands.command(name="ban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, i: discord.Interaction, member: discord.Member, reason: str = "No reason"):
        await member.ban(reason=reason)
        await i.response.send_message(
            embed=self._embed(f"🔨 **Banned** {member.mention}\nReason: {reason}")
        )

    @app_commands.command(name="unban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, i: discord.Interaction, user: discord.User):
        await i.guild.unban(user)
        await i.response.send_message(
            embed=self._embed(f"✅ **Unbanned** {user.mention}", discord.Color.green())
        )

    @app_commands.command(name="kick")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, i, member: discord.Member, reason: str = "No reason"):
        await member.kick(reason=reason)
        await i.response.send_message(
            embed=self._embed(f"👢 **Kicked** {member.mention}\nReason: {reason}")
        )

    @app_commands.command(name="timeout")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, i, member: discord.Member, minutes: int, reason: str = "No reason"):
        until = discord.utils.utcnow() + discord.timedelta(minutes=minutes)
        await member.timeout(until, reason=reason)
        await i.response.send_message(
            embed=self._embed(f"⏳ **Timed out** {member.mention} for {minutes}m\nReason: {reason}")
        )

    @app_commands.command(name="untimeout")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, i, member: discord.Member):
        await member.timeout(None)
        await i.response.send_message(
            embed=self._embed(f"✅ **Timeout removed** for {member.mention}", discord.Color.green())
        )

    @app_commands.command(name="warn")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, i, member: discord.Member, reason: str):
        count = self._add_warning(i.guild.id, member.id, reason)
        await i.response.send_message(
            embed=self._embed(f"⚠️ **Warned** {member.mention}\nReason: {reason}\nTotal warnings: {count}")
        )

    @app_commands.command(name="warnings")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, i, member: discord.Member):
        warns = self._get_warnings(i.guild.id, member.id)
        if not warns:
            await i.response.send_message(
                embed=self._embed(f"✅ {member.mention} has no warnings", discord.Color.green())
            )
            return

        text = "\n".join(
            f"{idx+1}. {w['reason']}" for idx, w in enumerate(warns)
        )
        await i.response.send_message(
            embed=self._embed(f"⚠️ **Warnings for {member.mention}**\n{text}")
        )

    @app_commands.command(name="clearwarns")
    @app_commands.checks.has_permissions(administrator=True)
    async def clearwarns(self, i, member: discord.Member):
        ok = self._clear_warnings(i.guild.id, member.id)
        msg = "🧹 **Warnings cleared**" if ok else "ℹ️ No warnings to clear"
        await i.response.send_message(embed=self._embed(msg))

    @app_commands.command(name="lock")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, i):
        await i.channel.set_permissions(i.guild.default_role, send_messages=False)
        await i.response.send_message(embed=self._embed("🔒 **Channel locked**"))

    @app_commands.command(name="unlock")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, i):
        await i.channel.set_permissions(i.guild.default_role, send_messages=True)
        await i.response.send_message(embed=self._embed("🔓 **Channel unlocked**"))

    @app_commands.command(name="slowmode")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self, i, seconds: int):
        await i.channel.edit(slowmode_delay=seconds)
        await i.response.send_message(
            embed=self._embed(f"🐢 **Slowmode set to {seconds}s**")
        )

    @app_commands.command(name="nick")
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def nick(self, i, member: discord.Member, nickname: str):
        await member.edit(nick=nickname)
        await i.response.send_message(
            embed=self._embed(f"✏️ **Nickname updated** for {member.mention}")
        )

    
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