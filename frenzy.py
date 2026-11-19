"""Fish Frenzy: she gives a clue, first to react with the right emoji wins the round."""
import asyncio
import random
import tomllib
from pathlib import Path
from typing import Callable

import discord

FILE = Path(__file__).parent / "frenzy.toml"
ROUNDS = 5
HINT_AFTER = 7    # seconds
ROUND_TIME = 16
NEXT_WAIT = 120   # how long the Next round button waits
QUIET = discord.AllowedMentions.none()  # name winners without pinging


def bare(emoji: str) -> str:
    # reactions and typed emoji differ on the variation selector
    return emoji.replace("️", "")


def load() -> list[dict]:
    return tomllib.loads(FILE.read_text(encoding="utf-8")).get("riddle", [])


class NextRound(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=NEXT_WAIT)
        self.pressed = asyncio.get_running_loop().create_future()

    @discord.ui.button(label="Next round", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        self.stop()
        await interaction.response.edit_message(view=self)
        if not self.pressed.done():
            self.pressed.set_result(True)

    async def on_timeout(self) -> None:
        if not self.pressed.done():
            self.pressed.set_result(False)


class Frenzy:
    def __init__(self, channel: discord.abc.Messageable, riddles: list[dict], on_end: Callable[[int, int], str]):
        # on_end(rounds played, right answers) -> extra text for the last message
        self.channel = channel
        self.riddles = random.sample(riddles, min(ROUNDS, len(riddles)))
        self.on_end = on_end
        self.scores: dict[int, int] = {}
        self.played = 0
        self.rights = 0
        self.msg_id: int | None = None  # round message taking answers
        self.answers: set[str] = set()
        self.winner: asyncio.Future | None = None

    def react(self, message_id: int, user_id: int, emoji: str) -> bool:
        """True if this reaction belongs to the game (so nothing else handles it)."""
        if message_id != self.msg_id:
            return False
        if bare(emoji) in self.answers and self.winner and not self.winner.done():
            self.winner.set_result(user_id)
        return True

    def board(self) -> discord.Embed:
        ranked = sorted(self.scores.items(), key=lambda kv: -kv[1])
        text = "\n".join(f"<@{uid}> · {pts}" for uid, pts in ranked) or "nobody yet… gyu"
        return discord.Embed(title="Leaderboard", description=text, color=0x8FD0F4)

    async def run(self) -> None:
        total = len(self.riddles)
        for r in self.riddles:
            text, embed = await self.round(r, total)
            if self.played == total:
                await self.channel.send(text + self.on_end(self.played, self.rights), embed=embed, allowed_mentions=QUIET)
                return
            view = NextRound()
            msg = await self.channel.send(text, embed=embed, view=view, allowed_mentions=QUIET)
            if not await view.pressed:
                await msg.edit(view=None)
                await self.channel.send("everyone swam off… *flop* game over" + self.on_end(self.played, self.rights), allowed_mentions=QUIET)
                return

    async def round(self, r: dict, total: int) -> tuple[str, discord.Embed]:
        self.played += 1
        self.answers = {bare(r["answer"]), *(bare(e) for e in r.get("also", []))}
        self.winner = asyncio.get_running_loop().create_future()
        text = f"**round {self.played}/{total}:** react with… *{r['clue']}*"
        msg = await self.channel.send(text)
        self.msg_id = msg.id
        hinted = False
        try:
            uid = await asyncio.wait_for(asyncio.shield(self.winner), HINT_AFTER)
        except TimeoutError:
            hinted = True
            await msg.edit(content=f"{text}\n> hint: {r['hint']}")
            try:
                uid = await asyncio.wait_for(self.winner, ROUND_TIME - HINT_AFTER)
            except TimeoutError:
                uid = None
        self.msg_id = None
        if uid is None:
            return f"time's up! it was {r['answer']} {r['name']}. nobody gets a point, gyu", self.board()
        pts = 1 if hinted else 2
        self.scores[uid] = self.scores.get(uid, 0) + pts
        self.rights += 1
        return f"{r['answer']} <@{uid}> got it first!! +{pts}", self.board()
