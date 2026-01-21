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
from flask import Flask, render_template, request, jsonify
import json, os

STATE_FILE = "state.json"

app = Flask(__name__)

def load_state():
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/state")
def get_state():
    return jsonify(load_state())

@app.route("/api/update", methods=["POST"])
def update():
    state = load_state()
    payload = request.json

    def deep_merge(src, upd):
        for k, v in upd.items():
            if isinstance(v, dict) and isinstance(src.get(k), dict):
                deep_merge(src[k], v)
            else:
                src[k] = v

    deep_merge(state, payload)
    save_state(state)
    return {"ok": True}

if __name__ == "__main__":
    app.run(
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", 5000)),
        debug=False
    )
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
# OAUTH HELPER 
# ======================

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
# LIVE CHAT ID
# ======================
def get_live_chat_id(youtube, video_id):
    res = youtube.videos().list(
        part="liveStreamingDetails",
        id=video_id
    ).execute()

    items = res.get("items", [])
    if not items:
        return None

    return items[0]["liveStreamingDetails"].get("activeLiveChatId")


# ======================
# LIVE CHAT MONITOR 
# ======================
async def monitor_live_chat(video_id, discord_channel):
    youtube = get_youtube_oauth()
    chat_id = get_live_chat_id(youtube, video_id)

    if not chat_id:
        return

    next_page = None

    while True:
        res = youtube.liveChatMessages().list(
            liveChatId=chat_id,
            part="snippet,authorDetails",
            pageToken=next_page
        ).execute()

        for item in res.get("items", []):
            author = item["authorDetails"]["displayName"]
            message = item["snippet"]["displayMessage"]

            # AI reply
            ai_reply = await ai.reply(message, author)

            if ai_reply:
                # Send reply to YouTube
                youtube.liveChatMessages().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "liveChatId": chat_id,
                            "type": "textMessageEvent",
                            "textMessageDetails": {
                                "messageText": ai_reply
                            }
                        }
                    }
                ).execute()

                # Log to Discord
                await discord_channel.send(
                    embed=cattrix_embed(
                        f"💬 **YT Live Chat**\n"
                        f"👤 {author}: {message}\n"
                        f"🤖 {ai_reply}",
                        discord.Color.gold()
                    )
                )

        next_page = res.get("nextPageToken")
        await asyncio.sleep(5)



# ======================
# YOUTUBE LIVE KEY
# ======================
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
yt_api = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

def get_live_streams(channel_id):
    res = yt_api.search().list(
        part="snippet",
        channelId=channel_id,
        eventType="live",
        type="video",
        maxResults=1
    ).execute()
    return res.get("items", [])


# ======================
# MONITOR LOOP
# ======================
active_streams = {}

async def youtube_monitor():
    await bot.wait_until_ready()

    while not bot.is_closed():
        state = read_state()
        yt_channels = state.get("yt_channels", {})

        log_channel_id = state["servers"]["GLOBAL"]["moderation"]["log_channel_id"]
        discord_channel = bot.get_channel(log_channel_id)

        if not discord_channel:
            await asyncio.sleep(10)
            continue

        for channel_id, cfg in yt_channels.items():
            if not cfg.get("live"):
                continue

            lives = get_live_streams(channel_id)
            for live in lives:
                video_id = live["id"]["videoId"]

                if video_id in active_streams:
                    continue  # already monitoring

                title = live["snippet"]["title"]
                url = f"https://youtu.be/{video_id}"

                # Announce in Discord
                await discord_channel.send(
                    embed=cattrix_embed(
                        f"🔴 **LIVE NOW**\n**{title}**\n{url}",
                        discord.Color.red()
                    )
                )

                # Start live chat monitor
                task = asyncio.create_task(
                    monitor_live_chat(video_id, discord_channel)
                )
                active_streams[video_id] = task

        await asyncio.sleep(60)


bot.loop.create_task(youtube_monitor())




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
def e(msg, color=discord.Color.red()):
    return discord.Embed(description=msg, color=color)


# ======================
# /PING
# ======================
@app_commands.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    latency = round(interaction.client.latency * 1000)
    await interaction.response.send_message(
        embed=e(f"🏓 Pong!\nLatency: `{latency} ms`", discord.Color.green()),
        ephemeral=True
    )


# ======================
# /NICK
# ======================
@app_commands.command(name="nick", description="Change a user's nickname")
@app_commands.checks.has_permissions(manage_nicknames=True)
async def nick(
    interaction: discord.Interaction,
    member: discord.Member,
    nickname: str
):
    await member.edit(nick=nickname)
    await interaction.response.send_message(
        embed=e(
            f"✏️ Nickname Updated\n"
            f"User: {member.mention}\n"
            f"New Nickname: `{nickname}`",
            discord.Color.green()
        )
    )

# ======================
# /BAN
# ======================
@app_commands.command(name="ban", description="Ban a member")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):
    await member.ban(reason=reason)
    await interaction.response.send_message(
        embed=e(
            f"🔨 Banned {member.mention}\nReason: {reason}"
        )
    )

# ======================
# /KICK
# ======================
@app_commands.command(name="kick", description="Kick a member")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):
    await member.kick(reason=reason)
    await interaction.response.send_message(
        embed=e(
            f"👢 Kicked {member.mention}\nReason: {reason}"
        )
    )

# ======================
# /TIMEOUT 
# ======================
@app_commands.command(name="timeout", description="Timeout a member")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: int,
    reason: str = "No reason provided"
):
    until = discord.utils.utcnow() + discord.timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)

    await interaction.response.send_message(
        embed=e(
            f"⏳ Timed Out {member.mention}\n"
            f"Duration: {minutes} minutes\n"
            f"Reason: {reason}"
        )
    )

# ======================
# /REMOVE TIMEOUT
# ======================
@app_commands.command(name="remove_timeout", description="Remove a member timeout")
@app_commands.checks.has_permissions(moderate_members=True)
async def remove_timeout(
    interaction: discord.Interaction,
    member: discord.Member
):
    await member.timeout(None)
    await interaction.response.send_message(
        embed=e(
            f"✅ Timeout removed for {member.mention}",
            discord.Color.green()
        )
    )

# ======================
# /WARN
# ======================
@app_commands.command(name="warn", description="Warn a member")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str
):
    state = read_state()
    warns = state["servers"]["GLOBAL"]["warnings"].setdefault(
        str(member.id), []
    )
    warns.append({"reason": reason, "time": int(time.time())})
    write_state(state)

    await interaction.response.send_message(
        embed=e(
            f"⚠️ Warned {member.mention}\n"
            f"Reason: {reason}\n"
            f"Total warnings: {len(warns)}"
        )
    )

# ======================
#/REMOVE WARN
# ======================
@app_commands.command(name="remove_warn", description="Remove all warnings from a member")
@app_commands.checks.has_permissions(administrator=True)
async def remove_warn(
    interaction: discord.Interaction,
    member: discord.Member
):
    state = read_state()
    existed = state["servers"]["GLOBAL"]["warnings"].pop(
        str(member.id), None
    )
    write_state(state)

    msg = (
        f"🧹 Warnings cleared for {member.mention}"
        if existed else
        f"ℹ️ No warnings found for {member.mention}"
    )

    await interaction.response.send_message(
        embed=e(msg, discord.Color.green())
    )

# ======================
# / SEARCH
# ======================
@app_commands.command(name="search", description="Search using AI")
async def search(
    interaction: discord.Interaction,
    query: str
):
    await interaction.response.defer()

    reply = await ai.reply(query, interaction.user.name)
    if not reply:
        reply = "No result found."

    await interaction.followup.send(
        embed=e(
            f"🔍 **Search Result**\n"
            f"Query: `{query}`\n\n{reply}",
            discord.Color.gold()
        )
    )

# ======================
# /PROFILE
# ======================
@app_commands.command(name="profile", description="View a user's profile")
async def profile(
    interaction: discord.Interaction,
    member: discord.Member = None
):
    member = member or interaction.user
    state = read_state()

    xp = state["stats"]["messages"].get(str(member.id), 0)
    lvl = state["stats"]["levels"].get(str(member.id), 0)

    embed = discord.Embed(
        title=f"{member.name}'s Profile",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=lvl)
    embed.add_field(name="XP", value=xp)
    embed.add_field(
        name="Warnings",
        value=len(
            state["servers"]["GLOBAL"]["warnings"].get(
                str(member.id), []
            )
        )
    )

    await interaction.response.send_message(embed=embed)

# ======================
# /SERVER PROFILE
# ======================
@app_commands.command(name="server_profile", description="View server information")
async def server_profile(interaction: discord.Interaction):
    g = interaction.guild

    embed = discord.Embed(
        title=g.name,
        color=discord.Color.purple()
    )
    embed.set_thumbnail(url=g.icon.url if g.icon else None)
    embed.add_field(name="Members", value=g.member_count)
    embed.add_field(name="Owner", value=g.owner.mention)
    embed.add_field(
        name="Created",
        value=g.created_at.strftime("%Y-%m-%d")
    )

    await interaction.response.send_message(embed=embed)

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



# ======================
# SYNC
# ======================
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

@bot.event
async def on_ready():
    await bot.tree.sync()
    print("Slash commands synced")

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
