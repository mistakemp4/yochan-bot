import json
import os
import random
import re
import time
import tomllib
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

ROOT = Path(__file__).parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
SEAL = CONFIG["seal"]
GYU = CONFIG["gyu"]
REACT = CONFIG["react"]
HUNGER = CONFIG["hunger"]
STATE_FILE = ROOT / "state.json"
MEDIA_DIR = ROOT / "media"
MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov"}
MAX_UPLOAD = 10 * 1024 * 1024

GYU_RE = re.compile(r"(?<![a-z])gy(u+)(?![a-z])", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+")
CUSTOM_EMOJI_RE = re.compile(r"<a?:(\w+):\d+>")
GYU_LETTERS = [
    "\N{REGIONAL INDICATOR SYMBOL LETTER G}",
    "\N{REGIONAL INDICATOR SYMBOL LETTER Y}",
    "\N{REGIONAL INDICATOR SYMBOL LETTER U}",
]
# hunger level -> lowest fullness % for it
LEVELS = [("full", 80), ("content", 50), ("peckish", 25), ("hungry", 10), ("starving", 0)]
HUNGRY = {"hungry", "starving"}

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

last_sent: dict[tuple[str, int], float] = {}
# message id -> letters in add order, None once answered
reaction_seq: OrderedDict[int, list[str] | None] = OrderedDict()
# guild id -> hunger state, saved to state.json
state: dict[str, dict] = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
synced = False


def ready(kind: str, channel_id: int) -> bool:
    now = time.monotonic()
    key = (kind, channel_id)
    if now - last_sent.get(key, float("-inf")) < CONFIG[kind]["cooldown"]:
        return False
    last_sent[key] = now
    return True


def has_keyword(text: str, words: list[str] | None = None) -> bool:
    text = text.lower()
    return any(k.lower() in text for k in words or SEAL["keywords"])


def wants_react(content: str) -> bool:
    if any(t in content for t in REACT["triggers"]):
        return True
    names = CUSTOM_EMOJI_RE.findall(content)
    return any(has_keyword(n, REACT["custom_keywords"]) for n in names)


def spells_gyu(seq: list[str]) -> bool:
    it = iter(seq)
    return all(letter in it for letter in GYU_LETTERS)


def gyu_word(n_u: int) -> str:
    style = random.choice(["lower", "upper", "title", "fade", "grow"])
    if style == "upper":
        return "GY" + "U" * n_u
    if style == "title":
        return "Gy" + "u" * n_u
    if style == "fade":
        k = random.randint(1, n_u)
        return "GY" + "U" * k + "u" * (n_u - k)
    if style == "grow":
        k = random.randint(0, n_u - 1)
        return "gy" + "u" * k + "U" * (n_u - k)
    return "gy" + "u" * n_u


def gyu_line(n_u: int) -> str:
    if GYU["lines"] and random.random() < GYU["line_chance"]:
        return random.choice(GYU["lines"])
    n_u = max(1, min(n_u, 12))
    line = gyu_word(n_u) + random.choice(GYU["punctuation"])
    if random.random() < GYU["kaomoji_chance"]:
        line += " " + random.choice(GYU["kaomoji"])
    if random.random() < GYU["action_chance"]:
        line = random.choice(GYU["actions"]) + " " + line
    if random.random() < GYU["emoji_chance"]:
        line += " " + random.choice(GYU["emoji"])
    return line


def save_state() -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def belly(guild_id: int) -> dict:
    return state.setdefault(
        str(guild_id), {"fullness": HUNGER["start"], "at": time.time(), "begged": None, "channel": None}
    )


def fullness(b: dict) -> float:
    drained = (time.time() - b["at"]) / 3600 * 100 / HUNGER["hours_to_empty"]
    return max(0.0, b["fullness"] - drained)


def level(full: float) -> str:
    return next(name for name, floor in LEVELS if full >= floor)


def meter(full: float) -> str:
    filled = round(full / 10)
    return f"{'▰' * filled}{'▱' * (10 - filled)} {int(full)}%"


def count_food(content: str) -> int:
    n = sum(content.count(f) for f in HUNGER["foods"])
    n += sum(has_keyword(name, HUNGER["custom_keywords"]) for name in CUSTOM_EMOJI_RE.findall(content))
    return min(n, HUNGER["max_fish"])


def is_food(emoji: discord.PartialEmoji) -> bool:
    if emoji.is_custom_emoji():
        return has_keyword(emoji.name or "", HUNGER["custom_keywords"])
    return emoji.name in HUNGER["foods"]


def feed(guild_id: int, fish: int) -> str:
    b = belly(guild_id)
    before = fullness(b)
    if before >= 95:
        return random.choice(HUNGER["refuse"])
    after = min(100.0, before + fish * HUNGER["per_fish"])
    b["fullness"], b["at"] = after, time.time()
    # beg again only if she's still hungry after eating
    b["begged"] = level(after) if level(after) in HUNGRY else None
    save_state()
    lines = HUNGER["eat_starving"] if level(before) in HUNGRY else HUNGER["eat"]
    return f"{random.choice(lines)}\n{meter(after)}"


def hungry_talk(guild_id: int) -> str | None:
    lvl = level(fullness(belly(guild_id)))
    if lvl in HUNGRY and random.random() < HUNGER["talk_chance"]:
        return random.choice(HUNGER[lvl])
    return None


async def is_seal(message: discord.Message) -> bool:
    if any(has_keyword(a.filename) for a in message.attachments):
        return True
    if any(has_keyword(url) for url in URL_RE.findall(message.content)):
        return True
    for item in message.stickers:
        if has_keyword(item.name):
            return True
        try:
            sticker = await item.fetch()
        except discord.HTTPException:
            continue
        extra = [getattr(sticker, "description", "") or "", getattr(sticker, "emoji", "") or ""]
        extra += getattr(sticker, "tags", [])
        if any(has_keyword(x) for x in extra):
            return True
    return False


def media_pool() -> list[tuple[str, object]]:
    files = sorted(p for p in MEDIA_DIR.iterdir() if p.suffix.lower() in MEDIA_EXTS)
    pool = [("file", p) for p in files]
    pool += [("link", url) for url in SEAL["links"]]
    pool += [("sticker", int(i)) for i in SEAL["sticker_ids"]]
    return pool


async def fetch_media(url: str) -> discord.File | None:
    # direct media links get uploaded, Discord's unfurler flakes on some
    name = unquote(Path(urlsplit(url).path).name)
    if Path(name).suffix.lower() not in MEDIA_EXTS:
        return None
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200 or (resp.content_length or 0) > MAX_UPLOAD:
                    return None
                buf = BytesIO()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    if buf.tell() + len(chunk) > MAX_UPLOAD:
                        return None
                    buf.write(chunk)
    except (aiohttp.ClientError, TimeoutError):
        return None
    buf.seek(0)
    return discord.File(buf, filename=name)


async def send_seal(message: discord.Message) -> None:
    pool = media_pool()
    if not pool:
        return
    kind, value = random.choice(pool)
    if kind == "file":
        await message.reply(file=discord.File(value), mention_author=False)
    elif kind == "link":
        file = await fetch_media(value)
        if file:
            await message.reply(file=file, mention_author=False)
        else:
            await message.reply(value, mention_author=False)
    else:
        sticker = client.get_sticker(value) or await client.fetch_sticker(value)
        await message.reply(stickers=[sticker], mention_author=False)


@tree.command(description="How hungry is Yo-chan?")
@app_commands.guild_only()
async def hunger(interaction: discord.Interaction):
    full = fullness(belly(interaction.guild_id))
    lvl = level(full)
    await interaction.response.send_message(f"{meter(full)} · {lvl}\n{random.choice(HUNGER[lvl])}")


@tasks.loop(minutes=HUNGER["beg_check_minutes"])
async def beg():
    for guild_id, b in state.items():
        lvl = level(fullness(b))
        if lvl not in HUNGRY or b["begged"] == lvl or not b["channel"]:
            continue
        b["begged"] = lvl
        save_state()
        channel = client.get_channel(b["channel"])
        if channel is None:
            continue
        try:
            await channel.send(random.choice(HUNGER[lvl]))
        except discord.HTTPException:
            pass


@client.event
async def on_ready():
    global synced
    print(f"logged in as {client.user} ({len(media_pool())} seal media)", flush=True)
    if not synced:
        # per-guild sync shows up instantly, global can lag
        for guild in client.guilds:
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        synced = True
    if not beg.is_running():
        beg.start()


@client.event
async def on_message(message: discord.Message):
    if message.guild is None:
        return
    if message.author == client.user:
        # she begs where she last talked
        belly(message.guild.id)["channel"] = message.channel.id
        save_state()
        return
    if message.author.bot:
        return
    match = GYU_RE.search(message.content)
    if match and ready("gyu", message.channel.id):
        line = hungry_talk(message.guild.id) or gyu_line(len(match.group(1)) + random.randint(0, 2))
        await message.reply(line, mention_author=False)
    if await is_seal(message) and ready("seal", message.channel.id):
        await send_seal(message)
    fish = count_food(message.content)
    if fish:
        line = feed(message.guild.id, fish)
        if ready("hunger", message.channel.id):
            await message.reply(line, mention_author=False)
    elif wants_react(message.content) and random.random() < REACT["chance"]:
        await message.add_reaction(random.choice(REACT["reactions"]))


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None or payload.user_id == client.user.id:
        return
    if payload.member and payload.member.bot:
        return
    if payload.message_author_id == client.user.id and is_food(payload.emoji):
        line = feed(payload.guild_id, 1)
        channel = client.get_channel(payload.channel_id)
        if channel and ready("hunger", payload.channel_id):
            await channel.get_partial_message(payload.message_id).reply(line, mention_author=False)
        return
    letter = payload.emoji.name
    if letter not in GYU_LETTERS:
        return
    seq = reaction_seq.get(payload.message_id, [])
    if seq is None:
        return
    if letter not in seq:
        seq.append(letter)
    reaction_seq[payload.message_id] = seq
    reaction_seq.move_to_end(payload.message_id)
    while len(reaction_seq) > 1000:
        reaction_seq.popitem(last=False)
    if letter != GYU_LETTERS[-1] or not spells_gyu(seq):
        return
    reaction_seq[payload.message_id] = None
    channel = client.get_channel(payload.channel_id)
    if channel is None or not ready("gyu", payload.channel_id):
        return
    target = channel.get_partial_message(payload.message_id)
    line = hungry_talk(payload.guild_id) or gyu_line(random.randint(2, 6))
    await target.reply(line, mention_author=False)


if __name__ == "__main__":
    client.run(os.environ["DISCORD_TOKEN"])
