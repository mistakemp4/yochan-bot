import asyncio
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

import activity
import frenzy
from gyusays import GyuSays

ROOT = Path(__file__).parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
SEAL = CONFIG["seal"]
GYU = CONFIG["gyu"]
REACT = CONFIG["react"]
HUNGER = CONFIG["hunger"]
HOME = CONFIG["home"]
FUN = CONFIG["fun"]
ACTIVITY = CONFIG["activity"]
STATE_FILE = ROOT / "state.json"
MEDIA_DIR = ROOT / "media"
PETS_DIR = ROOT / "pets"  # built by pet_build.py
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
# enrichment level -> lowest %
FUN_LEVELS = [("thrilled", 80), ("happy", 50), ("okay", 25), ("bored", 10), ("gloomy", 0)]
BORED = {"bored", "gloomy"}

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
web_runner = None


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
    # channel = last one she talked in, home = picked for auto messages
    return state.setdefault(
        str(guild_id),
        {"fullness": HUNGER["start"], "at": time.time(), "begged": None, "channel": None, "home": None},
    )


def can_post(channel: discord.abc.GuildChannel) -> bool:
    perms = channel.permissions_for(channel.guild.me)
    return perms.view_channel and perms.send_messages


def find_home(guild: discord.Guild) -> discord.TextChannel | None:
    # best auto_names match she can post in
    def rank(ch: discord.TextChannel) -> int | None:
        name = ch.name.lower()
        words = re.split(r"[-_]+", name)
        hits = [i for i, n in enumerate(HOME["auto_names"]) if name == n or n in words]
        return min(hits) if hits else None

    ranked = [(r, ch.position, ch) for ch in guild.text_channels if (r := rank(ch)) is not None and can_post(ch)]
    return min(ranked, key=lambda t: t[:2])[2] if ranked else None


def home_channel(guild_id: int) -> discord.abc.Messageable | None:
    # picked channel, else wherever she last talked
    b = belly(guild_id)
    for cid in (b.get("home"), b["channel"]):
        channel = client.get_channel(cid) if cid else None
        if channel is not None and can_post(channel):
            return channel
    return None


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


def fun_state(guild_id: int) -> dict:
    # older state files have no enrichment yet
    b = belly(guild_id)
    b.setdefault("fun", FUN["start"])
    b.setdefault("fun_at", time.time())
    return b


def fun_now(b: dict) -> float:
    drained = (time.time() - b["fun_at"]) / 3600 * 100 / FUN["hours_to_empty"]
    return max(0.0, b["fun"] - drained)


def fun_level(v: float) -> str:
    return next(name for name, floor in FUN_LEVELS if v >= floor)


def enrich(guild_id: int, amount: float) -> float:
    """Add enrichment, returns what was actually added."""
    b = fun_state(guild_id)
    before = fun_now(b)
    gain = 0.0 if before >= FUN["tired_at"] else min(100.0 - before, max(0.0, amount))
    b["fun"], b["fun_at"] = before + gain, time.time()
    # beg to play again only if she's still bored after
    b["fun_begged"] = fun_level(b["fun"]) if fun_level(b["fun"]) in BORED else None
    save_state()
    return gain


def gain_line(gain: float) -> str:
    return f"\n-# +{round(gain)}% enrichment" if gain >= 0.5 else "\n-# she's played out, no enrichment"


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


channel_group = app_commands.Group(
    name="channel",
    description="Where Yo-chan posts on her own",
    guild_only=True,
    default_permissions=discord.Permissions(manage_guild=True),
)


@channel_group.command(name="show", description="Show where Yo-chan posts on her own")
async def channel_show(interaction: discord.Interaction):
    b = belly(interaction.guild_id)
    if b.get("home"):
        text = f"i post my own messages in <#{b['home']}> gyu~"
    else:
        text = "no channel picked, i post wherever i last talked. use `/channel set` to pick one"
    await interaction.response.send_message(text, ephemeral=True)


@channel_group.command(name="set", description="Pick the channel Yo-chan posts in on her own")
@app_commands.describe(channel="Where she begs for fish and posts on her own")
async def channel_set(interaction: discord.Interaction, channel: discord.TextChannel):
    if not can_post(channel):
        await interaction.response.send_message(f"i can't send messages in {channel.mention} ;-; check my permissions there", ephemeral=True)
        return
    belly(interaction.guild_id)["home"] = channel.id
    save_state()
    await interaction.response.send_message(f"ok!! i'll post in {channel.mention} *flop*", ephemeral=True)


@channel_group.command(name="follow", description="Post wherever Yo-chan last talked instead")
async def channel_follow(interaction: discord.Interaction):
    belly(interaction.guild_id)["home"] = None
    save_state()
    await interaction.response.send_message("ok, i'll post wherever i last talked~", ephemeral=True)


tree.add_command(channel_group)


def auto_home(guild: discord.Guild) -> discord.TextChannel | None:
    # only for servers that never had one picked
    b = belly(guild.id)
    if b.get("home") or b.get("home_checked"):
        return None
    b["home_checked"] = True
    found = find_home(guild)
    b["home"] = found.id if found else None
    save_state()
    return found


@tree.command(description="How enriched is Yo-chan?")
@app_commands.guild_only()
async def enrichment(interaction: discord.Interaction):
    v = fun_now(fun_state(interaction.guild_id))
    lvl = fun_level(v)
    await interaction.response.send_message(f"{meter(v)} · {lvl}\n{random.choice(FUN[lvl])}")


@tree.command(description="Play Gyu Says: remember her snack order")
@app_commands.guild_only()
async def gyusays(interaction: discord.Interaction):
    guild_id = interaction.guild_id

    def on_end(cleared: int) -> str:
        return gain_line(enrich(guild_id, cleared * FUN["gyusays_per_round"])) if cleared else ""

    view = GyuSays(interaction.user.id, on_end)
    await interaction.response.send_message("gyu says…", view=view)
    view.message = await interaction.original_response()
    await view.next_round()


# guild id -> recent pat times, for pets_per_hour
pat_times: dict[int, list[float]] = {}
last_pat: dict[int, Path] = {}


def pat(guild_id: int) -> tuple[str, Path] | None:
    gifs = sorted(PETS_DIR.glob("*.gif")) if PETS_DIR.is_dir() else []
    if not gifs:
        return None
    # no same gif twice in a row
    choices = [g for g in gifs if g != last_pat.get(guild_id)] or gifs
    gif = last_pat[guild_id] = random.choice(choices)
    now = time.time()
    times = [t for t in pat_times.get(guild_id, []) if now - t < 3600] + [now]
    pat_times[guild_id] = times
    if len(times) > FUN["pets_per_hour"]:
        note = "\n-# that's a lot of pats this hour, no enrichment"
    else:
        note = gain_line(enrich(guild_id, FUN["pet"]))
    return random.choice(FUN["petted"]) + note, gif


class PetAgain(discord.ui.View):
    # persistent so old messages' buttons still work after restarts
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Pet again", emoji="✋", style=discord.ButtonStyle.primary, custom_id="pet:again")
    async def again(self, interaction: discord.Interaction, _button: discord.ui.Button):
        # fresh copy: after a restart every old button shares this view
        done = PetAgain()
        done.children[0].disabled = True
        await interaction.response.edit_message(view=done)
        await send_pat(interaction)


async def send_pat(interaction: discord.Interaction):
    got = pat(interaction.guild_id)
    if got is None:
        text = "no pat gifs yet… gyu"
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
        return
    text, gif = got
    file = discord.File(gif, filename="pet.gif")
    if interaction.response.is_done():
        await interaction.followup.send(text, file=file, view=PetAgain())
    else:
        await interaction.response.send_message(text, file=file, view=PetAgain())


@tree.command(description="Give Yo-chan head pats")
@app_commands.guild_only()
async def pet(interaction: discord.Interaction):
    await send_pat(interaction)


# channel id -> running Fish Frenzy
frenzies: dict[int, frenzy.Frenzy] = {}


@tree.command(description="Play Fish Frenzy: first to react with the right emoji wins")
@app_commands.guild_only()
async def fishfrenzy(interaction: discord.Interaction):
    channel_id, guild_id = interaction.channel_id, interaction.guild_id
    if channel_id in frenzies:
        await interaction.response.send_message("there's already a frenzy going here~", ephemeral=True)
        return
    riddles = frenzy.load()
    if not riddles:
        await interaction.response.send_message("i don't have any riddles… gyu", ephemeral=True)
        return

    def on_end(played: int, rights: int) -> str:
        return gain_line(enrich(guild_id, played * FUN["frenzy_per_round"] + rights * FUN["frenzy_per_correct"]))

    game = frenzies[channel_id] = frenzy.Frenzy(interaction.channel, riddles, on_end)
    try:
        await interaction.response.send_message(
            f"fish frenzy!! i describe something, first one to **react** with it wins. "
            f"2 points before my hint, 1 after. {len(game.riddles)} rounds go"
        )
        await game.run()
    except discord.HTTPException as e:
        print(f"fish frenzy stopped: {e}", flush=True)
    finally:
        frenzies.pop(channel_id, None)


class TidePlay(discord.ui.View):
    # persistent: the Play button keeps working after restarts
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Play Tide Pool", emoji="🌊", style=discord.ButtonStyle.primary, custom_id="tidepool:play")
    async def play(self, interaction: discord.Interaction, _button: discord.ui.Button):
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException as e:
            print(f"launch_activity failed: {e}", flush=True)
            await interaction.response.send_message("the game window isn't set up yet… gyu", ephemeral=True)


@tree.command(description="Play Tide Pool: get the water to Yo-chan's pool")
@app_commands.guild_only()
async def tidepool(interaction: discord.Interaction):
    await interaction.response.send_message(
        "gyu!! help me fill my pool *flop*\n-# turn the pipes so the water reaches me. longer paths count for more",
        view=TidePlay(),
    )


async def tide_result(user_id: int, guild_id: int | None, channel_id: int | None, res: dict) -> dict:
    # called by the web server once it has checked the board
    guild = client.get_guild(guild_id) if guild_id else None
    if guild is None:
        return {}
    try:
        await guild.fetch_member(user_id)
    except discord.HTTPException:
        return {}
    tiles = res["tiles"]
    gain = enrich(guild.id, tiles * FUN["tidepool_per_tile"] if res["won"] else FUN["tidepool_loss"])
    if res["won"]:
        extra = " · longer than my path, show-off ✨" if tiles > res["best"] else ""
        text = f"<@{user_id}> filled my pool!! {tiles} tiles of water *splash*{extra}"
    elif tiles:
        text = f"<@{user_id}> tried to fill my pool but it leaked ;-; {tiles} tiles of water"
    else:
        text = f"<@{user_id}>'s water didn't even get into the first pipe… gyu"
    channel = guild.get_channel_or_thread(channel_id) if channel_id else None
    if channel is None or not can_post(channel):
        channel = home_channel(guild.id)
    if channel is not None:
        try:
            await channel.send(text + gain_line(gain), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass
    return {"gain": gain}


@tasks.loop(minutes=HUNGER["beg_check_minutes"])
async def beg():
    for guild_id, b in list(state.items()):
        lvl = level(fullness(b))
        fun_lvl = fun_level(fun_now(fun_state(int(guild_id))))
        # one beg per check, food first
        if lvl in HUNGRY and b["begged"] != lvl:
            key, line = "begged", random.choice(HUNGER[lvl])
        elif fun_lvl in BORED and b.get("fun_begged") != fun_lvl:
            key, lvl, line = "fun_begged", fun_lvl, random.choice(FUN[fun_lvl])
        else:
            continue
        channel = home_channel(int(guild_id))
        if channel is None:
            continue
        b[key] = lvl
        save_state()
        try:
            await channel.send(line)
        except discord.HTTPException:
            pass


@client.event
async def on_ready():
    global synced, web_runner
    print(f"logged in as {client.user} ({len(media_pool())} seal media)", flush=True)
    if not synced:
        # per-guild sync shows up instantly, global can lag
        for guild in client.guilds:
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        synced = True
        client.add_view(TidePlay())
        client.add_view(PetAgain())
        web_runner = await activity.start(tide_result, ACTIVITY["port"])
    for guild in client.guilds:
        found = auto_home(guild)
        if found:
            print(f"{guild.name}: auto messages go to #{found.name}", flush=True)
    if not beg.is_running():
        beg.start()


@client.event
async def on_guild_join(guild: discord.Guild):
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)
    found = auto_home(guild)
    if found:
        try:
            await found.send("gyu!! i'll post my own stuff here. mods can change it with `/channel set` *flop*")
        except discord.HTTPException:
            pass


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
    game = frenzies.get(payload.channel_id)
    # answers on a round message aren't food or gyu letters
    if game and game.react(payload.message_id, payload.user_id, str(payload.emoji)):
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
