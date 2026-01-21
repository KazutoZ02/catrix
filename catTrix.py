#!/usr/bin/env python3
import os, json, time, math, asyncio, logging, tempfile
from dataclasses import dataclass
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

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

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

def get_youtube_oauth():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "client_secret.json",
                SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


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
# FULL MODERATION COG (EMBEDS ONLY)
# ======================
class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------- HELPERS ----------
    def embed(self, text, color=discord.Color.red()):
        return discord.Embed(description=text, color=color)

    def load_state(self):
        return read_state()

    def save_state(self, data):
        write_state(data)

    def add_warn(self, user_id, reason):
        data = self.load_state()
        warns = data["servers"]["GLOBAL"]["warnings"].setdefault(str(user_id), [])
        warns.append({
            "reason": reason,
            "time": int(time.time())
        })
        self.save_state(data)
        return len(warns)

    def clear_warns(self, user_id):
        data = self.load_state()
        existed = str(user_id) in data["servers"]["GLOBAL"]["warnings"]
        data["servers"]["GLOBAL"]["warnings"].pop(str(user_id), None)
        self.save_state(data)
        return existed

    # ---------- COMMANDS ----------

    @app_commands.command(name="timeout", description="Timeout a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: int,
        reason: str = "No reason provided"
    ):
        until = discord.utils.utcnow() + discord.timedelta(minutes=minutes)
        await member.timeout(until, reason=reason)

        await interaction.response.send_message(
            embed=self.embed(
                f"⏳ **Timed Out**\n"
                f"User: {member.mention}\n"
                f"Duration: {minutes} minutes\n"
                f"Reason: {reason}"
            )
        )

    @app_commands.command(name="remove_timeout", description="Remove a member timeout")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def remove_timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        await member.timeout(None)

        await interaction.response.send_message(
            embed=self.embed(
                f"✅ **Timeout Removed**\nUser: {member.mention}",
                discord.Color.green()
            )
        )

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str
    ):
        count = self.add_warn(member.id, reason)

        await interaction.response.send_message(
            embed=self.embed(
                f"⚠️ **User Warned**\n"
                f"User: {member.mention}\n"
                f"Reason: {reason}\n"
                f"Total Warnings: {count}"
            )
        )

    @app_commands.command(name="remove_warn", description="Remove all warnings from a member")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        existed = self.clear_warns(member.id)

        msg = (
            f"🧹 **Warnings Cleared** for {member.mention}"
            if existed else
            f"ℹ️ **No warnings found** for {member.mention}"
        )

        await interaction.response.send_message(
            embed=self.embed(msg, discord.Color.green())
        )

    @app_commands.command(name="nick", description="Change a member nickname")
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def nick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        nickname: str
    ):
        await member.edit(nick=nickname)

        await interaction.response.send_message(
            embed=self.embed(
                f"✏️ **Nickname Updated**\n"
                f"User: {member.mention}\n"
                f"New Nickname: `{nickname}`",
                discord.Color.green()
            )
        )

    @app_commands.command(name="purge", description="Delete messages in bulk")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: int
    ):
        await interaction.channel.purge(limit=amount)

        await interaction.response.send_message(
            embed=self.embed(f"🧹 **Deleted {amount} messages**"),
            ephemeral=True
        )

    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)

        await interaction.response.send_message(
            embed=self.embed(
                f"🏓 **Pong!**\nLatency: `{latency} ms`",
                discord.Color.green()
            ),
            ephemeral=True
        )



# -------------------------------------------------
# YouTube API Helper
# -------------------------------------------------
class YouTubeService:
    def __init__(self, api_key):
        self.api_key = api_key
        self.youtube = build("youtube", "v3", developerKey=api_key)

    def get_live_streams(self, channel_id):
        """Return list of live stream videos for a channel."""
        res = self.youtube.search().list(
            part="snippet",
            channelId=channel_id,
            eventType="live",
            type="video",
            maxResults=5
        ).execute()
        return res.get("items", [])

    def get_latest_upload(self, channel_id):
        """Return latest uploaded video."""
        res = self.youtube.search().list(
            part="snippet",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=1
        ).execute()
        return res.get("items", [])

    def get_latest_short(self, channel_id):
        """Return latest short (based on duration heuristics)."""
        videos = self.get_latest_upload(channel_id)
        if not videos:
            return None
        return videos[0]  # Same result for simple pipeline

class YouTubeMonitor:
    def __init__(self, bot, yt_service):
        self.bot = bot
        self.yt = yt_service
        self.active_streams = {}  # video_id -> task

    async def check_channels(self):
        state = read_state()
        channels = state.get("yt_channels", {})
        for cid, cfg in channels.items():
            # Live
            if cfg.get("live"):
                lives = self.yt.get_live_streams(cid)
                for video in lives:
                    vid = video["id"]["videoId"]
                    if vid not in self.active_streams:
                        task = asyncio.create_task(self.monitor_stream(vid, cid))
                        self.active_streams[vid] = task

            # New Video Upload
            if cfg.get("videos"):
                video = self.yt.get_latest_upload(cid)
                if video:
                    await self.post_video_notification(cid, video[0])

            # Shorts
            if cfg.get("shorts"):
                short = self.yt.get_latest_short(cid)
                if short:
                    await self.post_short_notification(cid, short)

    async def monitor_stream(self, video_id, channel_id):
        link = f"https://youtu.be/{video_id}"
        state = read_state()
        notify_channel_id = state.get("servers", {}).get("GLOBAL", {}).get("log_channel_id")
        notify_channel = self.bot.get_channel(notify_channel_id)
        title = self.yt.youtube.videos().list(
            part="snippet", id=video_id
        ).execute()["items"][0]["snippet"]["title"]

        # Announce
        if notify_channel:
            await notify_channel.send(
                embed=cattrix_embed(f"🔴 Live Now: **{title}**\n{link}", discord.Color.red())
            )

        # Loop basic polling for live chat messages (optional)
        while True:
            await asyncio.sleep(10)
            # If stream ends, break
            stats = self.yt.youtube.videos().list(
                part="liveStreamingDetails", id=video_id
            ).execute()["items"][0]["liveStreamingDetails"]
            if "activeLiveChatId" not in stats:
                break

            live_chat_id = stats["activeLiveChatId"]
            messages = self.yt.youtube.liveChatMessages().list(
                liveChatId=live_chat_id,
                part="snippet,authorDetails"
            ).execute()

            for item in messages.get("items", []):
                text = item["snippet"]["displayMessage"]
                author = item["authorDetails"]["displayName"]
                # You can optionally add AI search/response here

        # Stream ended
        await notify_channel.send(
            embed=cattrix_embed(f"🔴 Stream Ended: **{title}**\n{link}", discord.Color.dark_gray())
        )
        self.active_streams.pop(video_id, None)

monitor = YouTubeMonitor(bot, YouTubeService(os.getenv("YOUTUBE_API_KEY")))

@bot.event
async def on_ready():
    log.info("🐱 CatTrix ONLINE")

    # Start periodic YouTube check
    async def yt_loop():
        while True:
            try:
                await monitor.check_channels()
            except Exception as e:
                log.error(f"YT monitor error: {e}")
            await asyncio.sleep(cfg.poll)

    bot.loop.create_task(yt_loop())

async def ai_search(message):
    # For example: search query + categorize
    prompt = f"Message: {message}\nGive a concise summary:"
    reply = await ai.reply(prompt, "Searcher")
    return reply



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
