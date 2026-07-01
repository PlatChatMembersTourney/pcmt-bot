import os

import discord

from data_helpers import (
    list_regions, list_seasons, load_matches, match_file_path,
)
from build import build_event


class RegionSelect(discord.ui.Select):
    def __init__(self, regions):
        options = [discord.SelectOption(label=r.upper(), value=r) for r in regions][:25]
        super().__init__(placeholder="Select region", options=options)

    async def callback(self, interaction):
        self.view.region = self.values[0]
        self.view.rebuild()
        await interaction.response.edit_message(content=self.view.prompt(), view=self.view)


class SeasonSelect(discord.ui.Select):
    def __init__(self, seasons):
        options = [discord.SelectOption(label=s, value=s) for s in seasons][:25]
        super().__init__(placeholder="Select season", options=options)

    async def callback(self, interaction):
        self.view.season = self.values[0]
        self.view.rebuild()
        await interaction.response.edit_message(content=self.view.prompt(), view=self.view)


class DeleteMatchSelect(discord.ui.Select):
    def __init__(self, upcoming):
        options = []
        for m in upcoming[:25]:
            label = f"{m['team1']} vs {m['team2']} - {m.get('stage', '')}"[:90]
            options.append(discord.SelectOption(
                label=label, value=m["id"], description=m.get("date", "")[:10]))
        super().__init__(placeholder="Select the game to delete", options=options)

    async def callback(self, interaction):
        self.view.skeleton = self.view.upcoming_by_id[self.values[0]]
        self.view.rebuild()
        await interaction.response.edit_message(content=self.view.prompt(), view=self.view)


class ConfirmDeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Delete", style=discord.ButtonStyle.danger)

    async def callback(self, interaction):
        v = self.view
        m = v.skeleton
        path = match_file_path(v.region, v.season, m["id"])
        try:
            os.remove(path)
        except FileNotFoundError:
            v.stop()
            await interaction.response.edit_message(
                content=f"Already gone: no file for `{m['id']}`.", view=None)
            return

        build_note = ""
        try:
            build_event(v.region, v.season)
        except Exception as e:
            build_note = f"\n(build failed: {e})"
            print(f"build failed for {v.region}/{v.season}: {e}")

        v.stop()
        await interaction.response.edit_message(
            content=(
                f"Deleted **{m['team1Name']} vs {m['team2Name']}**\n"
                f"{m.get('stage', '')} | id `{m['id']}`\n"
                f"Removed events/{v.season}/{v.region}/matches/{m['id']}.json"
                f"{build_note}"
            ),
            view=None)


class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction):
        self.view.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing deleted.", view=None)


class DeleteView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.region = None
        self.season = None
        self.skeleton = None
        self.upcoming_by_id = {}
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your session.", ephemeral=True)
            return False
        return True

    def prompt(self):
        if self.region is None:
            return "Choose a region:"
        if self.season is None:
            return f"**{self.region.upper()}**\nChoose a season:"
        if self.skeleton is None:
            return f"**{self.region.upper()} / {self.season}**\nChoose the upcoming game to delete:"
        m = self.skeleton
        return (f"Delete **{m['team1Name']} vs {m['team2Name']}** "
                f"({m.get('stage', '')}, {m.get('date', '')[:10]})?\n"
                f"This removes the match file and can't be undone.")

    def rebuild(self):
        self.clear_items()
        if self.region is None:
            self.add_item(RegionSelect(list_regions()))
        elif self.season is None:
            self.add_item(SeasonSelect(list_seasons(self.region)))
        elif self.skeleton is None:
            upcoming = [m for m in load_matches(self.region, self.season) if not m.get("completed")]
            self.upcoming_by_id = {m["id"]: m for m in upcoming}
            if upcoming:
                self.add_item(DeleteMatchSelect(upcoming))
        else:
            self.add_item(ConfirmDeleteButton())
            self.add_item(CancelButton())


async def start(interaction):
    if not list_regions():
        await interaction.response.send_message("No regions found under events/.", ephemeral=True)
        return
    view = DeleteView(interaction.user.id)
    await interaction.response.send_message(view.prompt(), view=view, ephemeral=True)