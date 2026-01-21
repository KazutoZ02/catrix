#!/usr/bin/env python3
import os, json, time, asyncio, threading, logging
from typing import Dict, Optional
from dataclasses import dataclass, field

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import httpx

from flask import Flask, redirect, request, session, jsonify, render_template_string
from flask_cors import CORS

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# ======================
# ENV + LOGGING
# ======================
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CatTrix")

# ======================
# CONFIG
# ======================
@dataclass
class Config:
    discord_token: str = os.getenv("DISCORD_TOKEN")
    owner_id: int = int(os.getenv("BOT_OWNER_ID"))
    poll_interval: float = float(os.getenv("POLL_INTERVAL", 2))
    ai_cooldown: int = int(os.getenv("AI_COOLDOWN", 15))
    max_len: int = int(os.getenv("MAX_MESSAGE_LENGTH", 140))

# ======================
# AI PERSONALITIES
# ======================
AI_PERSONALITIES = {
    "cattrix": {
        "system": "You are CatTrix. Playful, clever, teasing but friendly.",
        "temp": 0.85,
        "tokens": 80
    },
    "chill": {
        "system": "You are calm, friendly, supportive.",
        "temp": 0.6,
        "tokens": 100
    },
    "mod": {
        "system": "You are a strict but polite moderator.",
        "temp": 0.4,
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
        self.personality = "cattrix"
        self.client = httpx.AsyncClient(timeout=20)
        self.key = os.getenv("OPENROUTER_API_KEY")
        self.model = os.getenv("OPENROUTER_MODEL")

    async def reply(self, message: str, author: str) -> Optional[str]:
        if time.time() - self.last_used < self.cfg.ai_cooldown:
            return None

        p = AI_PERSONALITIES[self.personality]
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": p["system"]},
                {"role": "user", "content": f"{author}: {message}"}
            ],
            "temperature": p["temp"],
            "max_tokens": p["tokens"]
        }

        r = await self.client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.key}"}
        )

        if r.status_code != 200:
            return None

        self.last_used = time.time()
        text = r.json()["choices"][0]["message"]["content"]
        return text[:self.cfg.max_len]

# ======================
# YOUTUBE OAUTH MANAGER
# ======================
class YouTubeOAuth:
    def __init__(self):
        self.tokens: Dict[str, Credentials] = {}

    def flow(self):
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                    "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                    "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=["https://www.googleapis.com/auth/youtube.force-ssl"]
        )

    def service(self, user_id: str):
        return build("youtube", "v3", credentials=self.tokens[user_id])

# ======================
# STREAM MANAGER (MULTI)
# ======================
class StreamManager:
    def __init__(self, ai: AIService, yt_oauth: YouTubeOAuth, cfg: Config):
        self.ai = ai
        self.oauth = yt_oauth
        self.cfg = cfg
        self.streams = {}

    def start(self, user_id: str, video_id: str):
        yt = self.oauth.service(user_id)
        chat_id = yt.videos().list(
            part="liveStreamingDetails", id=video_id
        ).execute()["items"][0]["liveStreamingDetails"]["activeLiveChatId"]

        self.streams[video_id] = {"yt": yt, "chat": chat_id, "page": None}
        asyncio.create_task(self.loop(video_id))

    async def loop(self, vid):
        s = self.streams[vid]
        while vid in self.streams:
            data = await asyncio.to_thread(
                s["yt"].liveChatMessages().list(
                    liveChatId=s["chat"],
                    part="snippet",
                    pageToken=s["page"]
                ).execute
            )
            s["page"] = data.get("nextPageToken")
            for i in data.get("items", []):
                msg = i["snippet"]["displayMessage"]
                author = i["snippet"]["authorDisplayName"]
                reply = await self.ai.reply(msg, author)
                if reply:
                    await asyncio.to_thread(
                        s["yt"].liveChatMessages().insert(
                            part="snippet",
                            body={"snippet":{
                                "liveChatId": s["chat"],
                                "type":"textMessageEvent",
                                "textMessageDetails":{"messageText":reply}
                            }}
                        ).execute
                    )
            await asyncio.sleep(self.cfg.poll_interval)

# ======================
# DISCORD MODERATION
# ======================
class Moderation(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="ban")
    async def ban(self, i: discord.Interaction, user: discord.Member, reason: str = None):
        await user.ban(reason=reason)
        await i.response.send_message("✅ User banned", ephemeral=True)

    @app_commands.command(name="kick")
    async def kick(self, i: discord.Interaction, user: discord.Member, reason: str = None):
        await user.kick(reason=reason)
        await i.response.send_message("✅ User kicked", ephemeral=True)

    @app_commands.command(name="purge")
    async def purge(self, i: discord.Interaction, amount: int):
        await i.channel.purge(limit=amount)
        await i.response.send_message("🧹 Messages deleted", ephemeral=True)

# ======================
# FLASK WEBSITE
# ======================
def start_web(bot):
    app = Flask(__name__)
    app.secret_key = os.getenv("WEB_SECRET_KEY")
    CORS(app)
    oauth = bot.yt_oauth

    HTML = """
    <h1>CatTrix Dashboard</h1>
    <a href="/login">Login YouTube</a>
    <form method="post" action="/personality">
      <select name="name"><option>cattrix</option><option>chill</option><option>mod</option></select>
      <button>Set Personality</button>
    </form>
    """

    @app.route("/")
    def home(): return render_template_string(HTML)

    @app.route("/login")
    def login():
        flow = oauth.flow()
        flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true"
        )
        session["state"] = state
        return redirect(auth_url)

    @app.route("/oauth/callback")
    def callback():
        flow = oauth.flow()
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        oauth.tokens["default"] = creds
        return redirect("/")

    @app.route("/personality", methods=["POST"])
    def personality():
        bot.ai.personality = request.form["name"]
        return redirect("/")

    app.run(
        host=os.getenv("WEB_HOST","0.0.0.0"),
        port=int(os.getenv("WEB_PORT",5000)),
        use_reloader=False
    )

# ======================
# DISCORD BOT
# ======================
class CatTrixBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

        self.cfg = Config()
        self.ai = AIService(self.cfg)
        self.yt_oauth = YouTubeOAuth()
        self.streams = StreamManager(self.ai, self.yt_oauth, self.cfg)

    async def setup_hook(self):
        await self.add_cog(Moderation(self))
        await self.tree.sync()

    async def on_ready(self):
        logger.info("CatTrix online as %s", self.user)

# ======================
# MAIN
# ======================
async def main():
    bot = CatTrixBot()
    threading.Thread(target=start_web, args=(bot,), daemon=True).start()
    await bot.start(bot.cfg.discord_token)

if __name__ == "__main__":
    asyncio.run(main())