import os
import random
import re
import time
import tomllib
from collections import OrderedDict
from pathlib import Path

import discord

ROOT = Path(__file__).parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
SEAL = CONFIG["seal"]
GYU = CONFIG["gyu"]
REACT = CONFIG["react"]
MEDIA_DIR = ROOT / "media"
MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov"}

GYU_RE = re.compile(r"(?<![a-z])gy(u+)(?![a-z])", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+")
CUSTOM_EMOJI_RE = re.compile(r"<a?:(\w+):\d+>")
GYU_LETTERS = [
    "\N{REGIONAL INDICATOR SYMBOL LETTER G}",
    "\N{REGIONAL INDICATOR SYMBOL LETTER Y}",
    "\N{REGIONAL INDICATOR SYMBOL LETTER U}",
]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

last_sent: dict[tuple[str, int], float] = {}
# message id -> letters in add order, None once answered
reaction_seq: OrderedDict[int, list[str] | None] = OrderedDict()


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


async def send_seal(message: discord.Message) -> None:
    pool = media_pool()
    if not pool:
        return
    kind, value = random.choice(pool)
    if kind == "file":
        await message.reply(file=discord.File(value), mention_author=False)
    elif kind == "link":
        await message.reply(value, mention_author=False)
    else:
        sticker = client.get_sticker(value) or await client.fetch_sticker(value)
        await message.reply(stickers=[sticker], mention_author=False)


@client.event
async def on_ready():
    print(f"logged in as {client.user} ({len(media_pool())} seal media)", flush=True)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    match = GYU_RE.search(message.content)
    if match and ready("gyu", message.channel.id):
        await message.reply(gyu_line(len(match.group(1)) + random.randint(0, 2)), mention_author=False)
    if await is_seal(message) and ready("seal", message.channel.id):
        await send_seal(message)
    if wants_react(message.content) and random.random() < REACT["chance"]:
        await message.add_reaction(random.choice(REACT["reactions"]))


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None or payload.user_id == client.user.id:
        return
    if payload.member and payload.member.bot:
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
    await target.reply(gyu_line(random.randint(2, 6)), mention_author=False)


if __name__ == "__main__":
    client.run(os.environ["DISCORD_TOKEN"])
