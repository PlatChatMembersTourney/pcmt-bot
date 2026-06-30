import discord
from datetime import datetime

from data_helpers import (
    FORMATS,
    list_regions, list_seasons, list_stages, load_teams, save_upcoming_match,
)
from timezone import TZ_OFFSETS, get_user_tz, set_user_tz, local_to_utc_iso
from build import build_event


# ---- dropdown components ----
class RegionSelect(discord.ui.Select):
    def __init__(self, regions):
        options = [discord.SelectOption(label=r.upper(), value=r) for r in regions][:25]
        super().__init__(placeholder="Select region", options=options, min_values=1, max_values=1)

    async def callback(self, interaction):
        self.view.region = self.values[0]
        await self.view.update(interaction)


class SeasonSelect(discord.ui.Select):
    def __init__(self, seasons):
        options = [discord.SelectOption(label=s, value=s) for s in seasons][:25]
        super().__init__(placeholder="Select season", options=options, min_values=1, max_values=1)

    async def callback(self, interaction):
        self.view.season = self.values[0]
        await self.view.update(interaction)


class TeamSelect(discord.ui.Select):
    def __init__(self, teams, slot, exclude=None):
        options = []
        for abbr, t in teams.items():
            if abbr == exclude:
                continue
            name = t.get("name", abbr) if isinstance(t, dict) else str(t)
            options.append(discord.SelectOption(label=f"{abbr} - {name}"[:100], value=abbr))
        options = options[:25]
        placeholder = "Select team 1" if slot == "team1" else "Select team 2"
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=1)
        self.slot = slot

    async def callback(self, interaction):
        if self.slot == "team1":
            self.view.team1 = self.values[0]
        else:
            self.view.team2 = self.values[0]
        await self.view.update(interaction)


class StageSelect(discord.ui.Select):
    def __init__(self, stages):
        options = [discord.SelectOption(label=s, value=s) for s in stages][:25]
        super().__init__(placeholder="Select stage", options=options, min_values=1, max_values=1)

    async def callback(self, interaction):
        self.view.stage = self.values[0]
        await self.view.update(interaction)


class FormatSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=f, value=f) for f in FORMATS]
        super().__init__(placeholder="Select format", options=options, min_values=1, max_values=1)

    async def callback(self, interaction):
        self.view.fmt = self.values[0]
        await self.view.update(interaction)


class TimezoneSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=tz, value=tz) for tz in TZ_OFFSETS][:25]
        super().__init__(placeholder="Select your timezone", options=options, min_values=1, max_values=1)

    async def callback(self, interaction):
        self.view.tz = self.values[0]
        set_user_tz(self.view.author_id, self.values[0])
        await self.view.update(interaction)


class DateButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Set date & time", style=discord.ButtonStyle.primary)

    async def callback(self, interaction):
        await interaction.response.send_modal(DateModal(self.view))


# ---- date entry popup ----
class DateModal(discord.ui.Modal, title="Match date & time"):
    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view
        self.date_input = discord.ui.TextInput(
            label="Date (YYYY-MM-DD)", placeholder="2026-06-01", required=True,
        )
        self.time_input = discord.ui.TextInput(
            label="Time (HH:MM, 24h)", placeholder="20:00", default="20:00", required=True,
        )
        self.add_item(self.date_input)
        self.add_item(self.time_input)

    async def on_submit(self, interaction):
        v = self.parent_view
        try:
            d = datetime.strptime(self.date_input.value.strip(), "%Y-%m-%d").date()
            t = datetime.strptime(self.time_input.value.strip(), "%H:%M").time()
        except ValueError:
            await interaction.response.send_message(
                "Couldn't read that. Use date YYYY-MM-DD and time HH:MM (e.g. 2026-06-01 and 20:00).",
                ephemeral=True)
            return

        date_iso = local_to_utc_iso(d, t, v.tz)

        teams = load_teams(v.region, v.season)
        match = save_upcoming_match(
            v.region, v.season, v.team1, v.team2, v.stage, FORMATS[v.fmt], date_iso, teams,
        )

        # Regenerate matches.json (and stats) so the new game shows up.
        build_note = ""
        try:
            build_event(v.region, v.season)
        except Exception as e:
            build_note = f"\n(build failed: {e})"
            print(f"build failed for {v.region}/{v.season}: {e}")

        v.stop()
        await interaction.response.edit_message(
            content=(
                f"Upcoming game created.\n"
                f"**{match['team1Name']}** vs **{match['team2Name']}**\n"
                f"{v.stage} | {v.fmt} | {date_iso} (entered as {v.tz})\n"
                f"{v.region.upper()} / {v.season}, id `{match['id']}`\n"
                f"Saved to events/{v.region}/{v.season}/matches.json{build_note}"
            ),
            view=None)


# ---- the multi-step view ----
class UpcomingView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.region = None
        self.season = None
        self.team1 = None
        self.team2 = None
        self.stage = None
        self.fmt = None
        self.tz = get_user_tz(author_id)
        self.tz_known = self.tz is not None
        self.build()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your setup session.", ephemeral=True)
            return False
        return True

    def prompt(self):
        total = 6 if self.tz_known else 7
        head = ""
        if self.region:
            head = f"**{self.region.upper()}"
            if self.season:
                head += f" / {self.season}"
            head += "**\n"
        if self.region is None:
            return f"Step 1 of {total}. Choose a region:"
        if self.season is None:
            return head + f"Step 2 of {total}. Choose a season:"
        if self.team1 is None:
            return head + f"Step 3 of {total}. Choose team 1:"
        if self.team2 is None:
            return head + f"Team 1 is **{self.team1}**. Step 4 of {total}. Choose team 2:"
        if self.stage is None:
            return head + f"**{self.team1}** vs **{self.team2}**. Step 5 of {total}. Choose stage:"
        if self.fmt is None:
            return head + f"**{self.team1}** vs **{self.team2}** | {self.stage}. Step 6 of {total}. Choose format:"
        if self.tz is None:
            return head + f"Step 7 of {total}. Choose your timezone (saved for next time):"
        return head + f"Times read as **{self.tz}**. Click below to enter the date and time."

    def build(self):
        self.clear_items()
        if self.region is None:
            self.add_item(RegionSelect(list_regions()))
        elif self.season is None:
            self.add_item(SeasonSelect(list_seasons(self.region)))
        elif self.team1 is None:
            self.add_item(TeamSelect(load_teams(self.region, self.season), "team1"))
        elif self.team2 is None:
            self.add_item(TeamSelect(load_teams(self.region, self.season), "team2", exclude=self.team1))
        elif self.stage is None:
            self.add_item(StageSelect(list_stages(self.region, self.season)))
        elif self.fmt is None:
            self.add_item(FormatSelect())
        elif self.tz is None:
            self.add_item(TimezoneSelect())
        else:
            self.add_item(DateButton())

    async def update(self, interaction):
        if self.season is None and not list_seasons(self.region):
            self.stop()
            await interaction.response.edit_message(
                content=f"No seasons found under events/{self.region}/.", view=None)
            return

        if self.season is not None and self.team1 is None and len(load_teams(self.region, self.season)) < 2:
            self.stop()
            await interaction.response.edit_message(
                content=f"Need at least 2 teams in events/{self.region}/{self.season}/teams.json.", view=None)
            return

        if self.team2 is not None and self.stage is None and not list_stages(self.region, self.season):
            self.stop()
            await interaction.response.edit_message(
                content=f"No stages defined in events/{self.region}/{self.season}/event.json.", view=None)
            return

        self.build()
        await interaction.response.edit_message(content=self.prompt(), view=self)