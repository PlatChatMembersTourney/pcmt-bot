import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv

import match
import delete
from data_helpers import list_regions
from timezone import TZ_OFFSETS, set_user_tz
from dropdown import UpcomingView

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.command()
async def sync(ctx):
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f"Synced {len(synced)} command(s) to this server.")


@bot.command()
async def ping(ctx):
    await ctx.send("pong")


@bot.tree.command(name="timezone", description="Set your timezone for match scheduling")
@app_commands.describe(zone="Your timezone")
@app_commands.choices(zone=[app_commands.Choice(name=tz, value=tz) for tz in TZ_OFFSETS])
async def timezone(interaction: discord.Interaction, zone: app_commands.Choice[str]):
    set_user_tz(interaction.user.id, zone.value)
    await interaction.response.send_message(
        f"Your timezone is set to **{zone.value}**. Match times you enter will use this.",
        ephemeral=True)


@bot.tree.command(name="upcoming", description="Set up an upcoming game")
async def upcoming(interaction: discord.Interaction):
    if not list_regions():
        await interaction.response.send_message("No regions found under events/.", ephemeral=True)
        return
    view = UpcomingView(interaction.user.id)
    await interaction.response.send_message(view.prompt(), view=view, ephemeral=True)


@bot.tree.command(name="match", description="Fill in a played game from tracker links")
async def match_cmd(interaction: discord.Interaction):
    await match.start(interaction)


@bot.tree.command(name="delete", description="Delete a scheduled game that hasn't been played")
async def delete_cmd(interaction: discord.Interaction):
    await delete.start(interaction)


bot.run(os.getenv("DISCORD_TOKEN"))