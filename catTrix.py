#!/usr/bin/env python3
import os, json, time, asyncio, threading, logging
from typing import Optional, Dict
from dataclasses import dataclass, field

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import httpx

from flask import Flask, jsonify, request, render_template_string
from flask_cors import CORS

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


async def setup_hook(self):
    await self.add_cog(Moderation(self))


# ======================
# ENV + LOGGING
# ======================
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CatTRIX")

# ======================
# CONFIG
# ======================
@dataclass
class Config:
    discord_token: str = os.getenv("DISCORD_TOKEN")
    poll_interval: float = float(os.getenv("POLL_INTERVAL", 2))
    ai_cooldown: int = int(os.getenv("AI_COOLDOWN", 15))
    max_message_length: int = int(os.getenv("MAX_MESSAGE_LENGTH", 140))

    ai_triggers: set = field(default_factory=lambda: {
        "CATTRIX", "@not.ur_CatTrix", "hey catTrix"
    })

# ======================
# AI PRESETS
# ======================
AI_PERSONALITIES = {
    "catTrix": {
        "system": "You are catTrix. Tsundere, witty, kind. Hinglish. Short replies.",
        "temp": 0.8,
        "tokens": 80
    },
    "chill": {
        "system": "You are calm, friendly, supportive.",
        "temp": 0.6,
        "tokens": 100
    },
    "roast": {
        "system": "You roast lightly but never insult.",
        "temp": 0.9,
        "tokens": 60
    }
}

# ======================
# AI SERVICE
# ======================
class AIService:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last_used = 0
        self.active_personality = "catTrix"
        self.client = httpx.AsyncClient(timeout=20)
        self.key = os.getenv("OPENROUTER_API_KEY")
        self.model = os.getenv("OPENROUTER_MODEL")

    async def reply(self, message: str, author: str) -> Optional[str]:
        if time.time() - self.last_used < self.cfg.ai_cooldown:
            return None
        if not any(t in message.lower() for t in self.cfg.ai_triggers):
            return None

        p = AI_PERSONALITIES[self.active_personality]

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": p["system"]},
                {"role": "user", "content": f"{author}: {message}"}
            ],
            "temperature": p["temp"],
            "max_tokens": p["tokens"]
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

# ======================
# YOUTUBE SERVICE
# ======================
class YouTubeService:
    def __init__(self):
        self.youtube = None

    def auth(self):
        creds = Credentials.from_authorized_user_info(
            json.loads(os.getenv("TOKEN_JSON"))
        )
        if creds.expired:
            creds.refresh(Request())
        self.youtube = build("youtube", "v3", credentials=creds)

    def chat_id(self, video_id):
        r = self.youtube.videos().list(
            part="liveStreamingDetails",
            id=video_id
        ).execute()
        return r["items"][0]["liveStreamingDetails"]["activeLiveChatId"]

    def messages(self, chat_id, page=None):
        return self.youtube.liveChatMessages().list(
            liveChatId=chat_id,
            part="snippet",
            pageToken=page
        ).execute()

    def send(self, chat_id, text):
        self.youtube.liveChatMessages().insert(
            part="snippet",
            body={"snippet":{
                "liveChatId": chat_id,
                "type":"textMessageEvent",
                "textMessageDetails":{"messageText":text}
            }}
        ).execute()

# ======================
# MULTI-STREAM MANAGER
# ======================
class StreamManager:
    def __init__(self, yt, ai, cfg):
        self.yt = yt
        self.ai = ai
        self.cfg = cfg
        self.streams: Dict[str, dict] = {}

    def start(self, video_id):
        if video_id in self.streams:
            return False
        chat_id = self.yt.chat_id(video_id)
        self.streams[video_id] = {
            "chat_id": chat_id,
            "page": None,
            "running": True
        }
        asyncio.create_task(self.loop(video_id))
        return True

    async def loop(self, vid):
        s = self.streams[vid]
        while s["running"]:
            data = await asyncio.to_thread(
                self.yt.messages, s["chat_id"], s["page"]
            )
            s["page"] = data.get("nextPageToken")
            for i in data.get("items", []):
                msg = i["snippet"]["displayMessage"]
                author = i["snippet"]["authorDisplayName"]
                reply = await self.ai.reply(msg, author)
                if reply:
                    await asyncio.to_thread(self.yt.send, s["chat_id"], reply)
            await asyncio.sleep(self.cfg.poll_interval)

# ======================
# FLASK WEBSITE
# ======================
def start_web(bot):
    app = Flask(__name__)
    CORS(app)

    HTML = """
    <h1>catTrix Dashboard</h1>
    <form action="/start" method="post">
      <input name="video_id" placeholder="YouTube Video ID">
      <button>Start Stream</button>
    </form>
    <form action="/personality" method="post">
      <select name="name">
        <option>catTrix
</option>
        <option>chill</option>
        <option>roast</option>
      </select>
      <button>Set Personality</button>
    </form>
    """

    @app.route("/")
    def home(): return render_template_string(HTML)

    @app.route("/start", methods=["POST"])
    def start_stream():
        return jsonify(
            success=bot.streams.start(request.form["video_id"])
        )

    @app.route("/personality", methods=["POST"])
    def set_p():
        bot.ai.active_personality = request.form["name"]
        return "OK"

    app.run(
        host=os.getenv("WEB_HOST","0.0.0.0"),
        port=int(os.getenv("WEB_PORT",5000)),
        use_reloader=False
    )

# ======================
# DISCORD BOT
# ======================
class RukiyaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="/", intents=intents)

        self.cfg = Config()
        self.ai = AIService(self.cfg)
        self.yt = YouTubeService()
        self.yt.auth()
        self.streams = StreamManager(self.yt, self.ai, self.cfg)

    async def on_ready(self):
        logger.info("Logged in as catTrix", self.user)

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.warnings = {}  # {user_id: [reasons]}

    # -----------------
    # PURGE
    # -----------------
    @app_commands.command(name="purge", description="Delete multiple messages")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(
            f"🧹 Deleted **{len(deleted)}** messages.",
            ephemeral=True
        )

    # -----------------
    # MUTE / TIMEOUT
    # -----------------
    @app_commands.command(name="mute", description="Timeout a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: int,
        reason: str = "No reason provided"
    ):
        await member.timeout(
            discord.utils.utcnow() + discord.timedelta(minutes=minutes),
            reason=reason
        )
        await interaction.response.send_message(
            f"🔇 {member.mention} muted for **{minutes} minutes**.\n📝 {reason}"
        )

    @app_commands.command(name="unmute", description="Remove timeout from a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        await member.timeout(None)
        await interaction.response.send_message(
            f"🔊 {member.mention} has been unmuted."
        )

    # -----------------
    # KICK / BAN
    # -----------------
    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided"
    ):
        await member.kick(reason=reason)
        await interaction.response.send_message(
            f"👢 {member.mention} was kicked.\n📝 {reason}"
        )

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided"
    ):
        await member.ban(reason=reason)
        await interaction.response.send_message(
            f"🔨 {member.mention} was banned.\n📝 {reason}"
        )

    # -----------------
    # WARN
    # -----------------
    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str
    ):
        self.warnings.setdefault(member.id, []).append(reason)
        count = len(self.warnings[member.id])

        await interaction.response.send_message(
            f"⚠️ {member.mention} warned.\n"
            f"📝 Reason: {reason}\n"
            f"📊 Total warnings: {count}"
        )

    # -----------------
    # LOCK / UNLOCK
    # -----------------
    @app_commands.command(name="lock", description="Lock the current channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction):
        await interaction.channel.set_permissions(
            interaction.guild.default_role,
            send_messages=False
        )
        await interaction.response.send_message("🔒 Channel locked.")

    @app_commands.command(name="unlock", description="Unlock the current channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction):
        await interaction.channel.set_permissions(
            interaction.guild.default_role,
            send_messages=True
        )
        await interaction.response.send_message("🔓 Channel unlocked.")


# ======================
# MAIN
# ======================
async def main():
    bot = RukiyaBot()
    threading.Thread(target=start_web, args=(bot,), daemon=True).start()
    await bot.start(bot.cfg.discord_token)

if __name__ == "__main__":
    asyncio.run(main())
