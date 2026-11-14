"""Gyu Says: Simon Says with snacks, played with buttons."""
import asyncio
import random
from typing import Callable

import discord

SNACKS = ["🐟", "🦐", "🦑", "🐙"]
MAX_ROUNDS = 20
HEADER = "gyu says: remember my snack order and feed it back to me~"


class GyuSays(discord.ui.View):
    def __init__(self, player_id: int, on_end: Callable[[int], str]):
        # on_end(rounds cleared) -> extra text for the last message
        super().__init__(timeout=60)
        self.player_id = player_id
        self.on_end = on_end
        self.seq: list[str] = []
        self.pos = 0
        self.accepting = False
        self.done = False
        self.message: discord.Message | None = None
        for snack in SNACKS:
            button = discord.ui.Button(emoji=snack, style=discord.ButtonStyle.secondary)
            button.callback = self._press(snack)
            self.add_item(button)

    def _lock(self, locked: bool) -> None:
        for item in self.children:
            item.disabled = locked

    def _text(self, line: str, sub: str) -> str:
        return f"{HEADER}\n**round {len(self.seq)}** · {line}\n-# {sub}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("this is someone else's game~ start your own with /gyusays", ephemeral=True)
            return False
        return True

    async def next_round(self) -> None:
        self.seq.append(random.choice(SNACKS))
        self.pos, self.accepting = 0, False
        self._lock(True)
        await self.message.edit(content=self._text(" ".join(self.seq), "memorize it!"), view=self)
        await asyncio.sleep(1.5 + 0.5 * len(self.seq))
        if self.done:
            return
        self._lock(False)
        self.accepting = True
        await self.message.edit(content=self._text("▒ " * len(self.seq), "your turn~"), view=self)

    def _press(self, snack: str):
        async def callback(interaction: discord.Interaction):
            if self.done or not self.accepting:
                await interaction.response.defer()
                return
            want = self.seq[self.pos]
            if snack != want:
                await self._finish(interaction, f"nooo that was {snack}, i wanted {want}. you made it to **round {len(self.seq)}**", len(self.seq) - 1)
                return
            self.pos += 1
            if self.pos < len(self.seq):
                await interaction.response.edit_message(content=self._text("✓ " * self.pos, "keep going~"), view=self)
                return
            if len(self.seq) >= MAX_ROUNDS:
                await self._finish(interaction, f"you remembered all {MAX_ROUNDS} rounds!!! *happy flipper flaps*", MAX_ROUNDS)
                return
            self.accepting = False
            self._lock(True)
            line = random.choice(["*nom nom* correct!!", "gyu~ perfect", "*happy flop*"])
            await interaction.response.edit_message(content=self._text("✓ " * self.pos, line), view=self)
            await asyncio.sleep(0.8)
            if not self.done:
                await self.next_round()

        return callback

    async def _finish(self, interaction: discord.Interaction | None, text: str, cleared: int) -> None:
        self.done, self.accepting = True, False
        self._lock(True)
        self.stop()
        content = f"{HEADER}\n{text}{self.on_end(cleared)}"
        if interaction:
            await interaction.response.edit_message(content=content, view=self)
        elif self.message:
            await self.message.edit(content=content, view=self)

    async def on_timeout(self) -> None:
        if not self.done:
            cleared = max(0, len(self.seq) - 1)
            try:
                await self._finish(None, f"too slow… *flop* you made it to **round {len(self.seq)}**", cleared)
            except discord.HTTPException:
                pass
