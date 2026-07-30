import asyncio

import discord

from data_helpers import list_regions, list_seasons, git_sync
from build import build_event


class RegionSelect(discord.ui.Select):
    def __init__(self, regions):
        options = [discord.SelectOption(label=r.upper(), value=r) for r in regions][:25]
        super().__init__(placeholder="Select region", options=options)

    async def callback(self, interaction):
        view = self.view
        view.region = self.values[0]
        view.rebuild()
        await interaction.response.edit_message(content=view.prompt(), view=view)


class SeasonSelect(discord.ui.Select):
    def __init__(self, seasons):
        options = [discord.SelectOption(label=s, value=s) for s in seasons][:25]
        super().__init__(placeholder="Select season", options=options)

    async def callback(self, interaction):
        view = self.view
        view.season = self.values[0]
        view.rebuild()
        await interaction.response.edit_message(content=view.prompt(), view=view)


class BuildButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Build", style=discord.ButtonStyle.success)

    async def callback(self, interaction):
        v = self.view
        v.stop()
        await interaction.response.edit_message(content="Building…", view=None)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, build_event, v.region, v.season)
        except Exception as e:
            print(f"build failed for {v.region}/{v.season}: {e}")
            await interaction.channel.send(
                f"Build failed for **{v.region.upper()} / {v.season}**: {e}")
            return

        sync_note = await loop.run_in_executor(
            None, git_sync, f"bot: rebuild {v.region}/{v.season}")

        await interaction.channel.send(
            f"Rebuilt **{v.region.upper()} / {v.season}**"
            f"{sync_note or ' — no data changes to push.'}")


class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction):
        self.view.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing built.", view=None)


class BuildView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.region = None
        self.season = None
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
        return (f"Rebuild **{self.region.upper()} / {self.season}**?\n"
                f"This regenerates matches.json, standings, and all stat files "
                f"from the existing games, then pushes to the site.")

    def rebuild(self):
        self.clear_items()
        if self.region is None:
            self.add_item(RegionSelect(list_regions()))
        elif self.season is None:
            self.add_item(SeasonSelect(list_seasons(self.region)))
        else:
            self.add_item(BuildButton())
            self.add_item(CancelButton())


async def start(interaction):
    if not list_regions():
        await interaction.response.send_message("No regions found under events/.", ephemeral=True)
        return
    view = BuildView(interaction.user.id)
    await interaction.response.send_message(view.prompt(), view=view, ephemeral=True)
