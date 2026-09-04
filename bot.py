import asyncio
from datetime import datetime, timezone, timedelta
import json
import os
import random
from typing import Literal
import discord
from discord import app_commands
from discord.ext import commands, tasks

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "blacklists.json"
WARNS_FILE = "warns.json"


def load_data_file(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_data_file(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)


saved_data_db = load_data_file(DATA_FILE)
warns_db = load_data_file(WARNS_FILE)


def clean_expired_warns():
    now = datetime.now(timezone.utc).timestamp()
    updated = False
    for user_id in list(warns_db.keys()):
        active_warns = []
        for warn in warns_db[user_id]:
            expires_at = warn.get("expires_at")
            if expires_at is None or now < expires_at:
                active_warns.append(warn)
            else:
                updated = True
        if len(active_warns) != len(warns_db[user_id]):
            warns_db[user_id] = active_warns
            updated = True
        if not warns_db[user_id]:
            del warns_db[user_id]
    if updated:
        save_data_file(WARNS_FILE, warns_db)


def has_custom_role_or_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    allowed_role_ids = {
        1535017310866243636,
        1535017259150344343,
        1535017205090095145,
        1535017157371498646,
        1535017134256685318,
        1535017046629159043,
        1535016938051211445,
        1535331000681369670,
    }
    return any(role.id in allowed_role_ids for role in interaction.user.roles)


def check_hierarchy(interaction: discord.Interaction, target: discord.Member) -> bool:
    if interaction.user == interaction.guild.owner:
        return True
    if target == interaction.guild.owner:
        return False
    return interaction.user.top_role > target.top_role


def get_target_channel(guild: discord.Guild, channel_name: str) -> discord.TextChannel:
    for channel in guild.text_channels:
        if channel.name.lower() == channel_name.lower():
            return channel
    return None


class BlacklistConfirmView(discord.ui.View):

    def __init__(self, interaction: discord.Interaction, target_id: int):
        super().__init__(timeout=60)
        self.orig_interaction = interaction
        self.target_id = target_id
        self.value = None

    @discord.ui.button(
        label="Confirm Blacklist", style=discord.ButtonStyle.green, emoji="✅"
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.orig_interaction.user:
            await interaction.response.send_message(
                "❌ Only the moderator who initiated this command can confirm it.",
                ephemeral=True,
            )
            return
        
        await interaction.response.defer()
        self.value = True
        self.stop()
        try:
            await interaction.message.delete()
        except Exception:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌")
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.orig_interaction.user:
            await interaction.response.send_message(
                "❌ Only the moderator who initiated this command can cancel it.",
                ephemeral=True,
            )
            return

        self.value = False
        self.stop()

        target_id_str = str(self.target_id)
        if target_id_str in saved_data_db:
            saved_data_db.pop(target_id_str)
            save_data_file(DATA_FILE, saved_data_db)

        try:
            await interaction.response.edit_message(content="❌ Blacklist action cancelled.", embed=None, view=None)
            await asyncio.sleep(4)
            await interaction.message.delete()
        except Exception:
            pass


class UnblacklistConfirmView(discord.ui.View):

    def __init__(self, interaction: discord.Interaction, target_id: int):
        super().__init__(timeout=60)
        self.orig_interaction = interaction
        self.target_id = target_id
        self.value = None

    @discord.ui.button(
        label="Confirm Unblacklist", style=discord.ButtonStyle.green, emoji="✅"
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.orig_interaction.user:
            await interaction.response.send_message(
                "❌ Only the moderator who initiated this command can confirm it.",
                ephemeral=True,
            )
            return
        
        await interaction.response.defer()
        self.value = True
        self.stop()
        try:
            await interaction.message.delete()
        except Exception:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌")
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.orig_interaction.user:
            await interaction.response.send_message(
                "❌ Only the moderator who initiated this command can cancel it.",
                ephemeral=True,
            )
            return

        self.value = False
        self.stop()

        try:
            await interaction.response.edit_message(content="❌ Unblacklist action cancelled.", embed=None, view=None)
            await asyncio.sleep(4)
            await interaction.message.delete()
        except Exception:
            pass


@bot.event
async def on_ready():
    clean_expired_warns()
    check_warn_expiry.start()
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synced {len(synced)} global commands!")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    print(f"Bot is online as {bot.user}!")


@tasks.loop(minutes=5)
async def check_warn_expiry():
    clean_expired_warns()


@bot.event
async def on_member_join(member: discord.Member):
    user_id_str = str(member.id)
    if user_id_str in saved_data_db:
        guild = member.guild
        blacklist_role = discord.utils.get(guild.roles, name="Blacklisted")
        if blacklist_role:
            try:
                old_nickname = member.nick or member.name
                member_roles = [r for r in member.roles if r != guild.default_role]
                role_ids = [r.id for r in member_roles]

                if not saved_data_db[user_id_str].get("roles"):
                    saved_data_db[user_id_str]["roles"] = role_ids
                    saved_data_db[user_id_str]["old_nickname"] = old_nickname
                    save_data_file(DATA_FILE, saved_data_db)

                new_nick = f"Blacklisted [{member.name}]"
                if len(new_nick) > 32:
                    new_nick = f"Blacklisted [{member.name[:15]}]"

                await member.edit(nick=new_nick)
                if member_roles:
                    await member.remove_roles(*member_roles)
                await member.add_roles(blacklist_role)
            except Exception:
                pass


@bot.tree.command(
    name="blacklist", description="Blacklist a member by user or user ID"
)
@app_commands.describe(
    user="The member or user ID to blacklist", reason="Reason", category="Category"
)
async def blacklist(
    interaction: discord.Interaction,
    user: str,
    reason: str,
    category: Literal["Appealable⚖️", "Bail only💰", "Permanent⛔"],
):
    is_admin = interaction.user.guild_permissions.administrator
    has_blacklist_role = any(
        role.id == 1538119694928842762 for role in interaction.user.roles
    )

    if not (is_admin or has_blacklist_role):
        await interaction.response.send_message(
            "❌ You do not have permission to use this command.", ephemeral=True
        )
        return

    if not is_admin and interaction.channel.name.lower() != "《➦》blacklist".lower():
        await interaction.response.send_message(
            "❌ You can only use the blacklist command in the 《➦》blacklist channel.",
            ephemeral=True,
        )
        return

    clean_id = user.strip("<@!> ")
    if not clean_id.isdigit():
        await interaction.response.send_message(
            "❌ Please provide a valid user or user ID.", ephemeral=True
        )
        return

    target_id = int(clean_id)
    guild = interaction.guild
    member = guild.get_member(target_id)

    if member and not check_hierarchy(interaction, member):
        await interaction.response.send_message(
            "You cannot blacklist this member due to having an equal or higher role then yours",
            ephemeral=True,
        )
        return

    member_avatar = member.avatar.url if member and member.avatar else None

    # Clean & Stylish Blacklist Embed Design (Server Restricted)
    embed = discord.Embed(
        title="🚫 User Blacklisted",
        description="> A user has been restricted from the server.",
        color=16730183,
    )
    if member_avatar:
        embed.set_thumbnail(url=member_avatar)
    elif guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(
        name="👤 Target User",
        value=f"<@{target_id}>\n`ID: {target_id}`",
        inline=True,
    )
    embed.add_field(
        name="🛡️ Moderated By",
        value=f"{interaction.user.mention}",
        inline=True,
    )
    embed.add_field(
        name="📂 Category",
        value=f"**{category}**",
        inline=False,
    )
    embed.add_field(
        name="📝 Reason",
        value=f"> {reason}",
        inline=False,
    )
    embed.set_footer(text="TSB Sweden Security System")
    embed.timestamp = datetime.now(timezone.utc)

    view = BlacklistConfirmView(interaction, target_id)
    await interaction.response.send_message(embed=embed, view=view)
    await view.wait()

    if not view.value:
        return

    blacklist_role = discord.utils.get(guild.roles, name="Blacklisted")
    role_ids = []
    if member:
        role_ids = [r.id for r in member.roles if r != guild.default_role]

    saved_data_db[str(target_id)] = {
        "roles": role_ids,
        "reason": reason,
        "category": category,
    }
    save_data_file(DATA_FILE, saved_data_db)

    if member:
        try:
            old_nick = member.nick or member.name
            saved_data_db[str(target_id)]["old_nickname"] = old_nick
            save_data_file(DATA_FILE, saved_data_db)
        except Exception:
            pass

        await member.edit(nick=f"Blacklisted [{member.name}]")
        if role_ids:
            await member.remove_roles(
                *(guild.get_role(r) for r in role_ids if guild.get_role(r))
            )
        if blacklist_role:
            await member.add_roles(blacklist_role)

        try:
            dm_text = (
                f"🚫 You have been **blacklisted** from **{guild.name}**\n\n"
                f"**Reason:** {reason}\n"
                f"**Category:** {category}\n\n"
            )
            if category == "Appealable⚖️":
                dm_text += "⚖️ This blacklist is appealable."
            elif category == "Bail only💰":
                dm_text += "💰 This blacklist can be resolved via bail only."
            else:
                dm_text += "⛔ This blacklist is permanent."

            target_user_obj = (
                member
                if isinstance(member, discord.User)
                else await bot.fetch_user(target_id)
            )
            await target_user_obj.send(dm_text)
        except Exception:
            pass

    log_embed = discord.Embed(
        title="🚫 User Blacklisted",
        description="> A user has been restricted from the server.",
        color=16730183,
    )
    if member_avatar:
        log_embed.set_thumbnail(url=member_avatar)
    elif guild.icon:
        log_embed.set_thumbnail(url=guild.icon.url)

    log_embed.add_field(
        name="👤 Target User",
        value=f"<@{target_id}>\n`ID: {target_id}`",
        inline=True,
    )
    log_embed.add_field(
        name="🛡️ Moderated By",
        value=f"{interaction.user.mention}",
        inline=True,
    )
    log_embed.add_field(
        name="📂 Category",
        value=f"**{category}**",
        inline=False,
    )
    log_embed.add_field(
        name="📝 Reason",
        value=f"> {reason}",
        inline=False,
    )
    log_embed.set_footer(text="TSB Sweden Security System")
    log_embed.timestamp = datetime.now(timezone.utc)

    target_channel = get_target_channel(guild, "《➦》blacklist")
    if target_channel:
        await target_channel.send(embed=log_embed)
    else:
        await interaction.channel.send(embed=log_embed)


@bot.tree.command(
    name="unblacklist", description="Remove a user from the blacklist"
)
@app_commands.describe(user="User mention or User ID to unblacklist", reason="Reason")
async def unblacklist(interaction: discord.Interaction, user: str, reason: str):
    is_admin = interaction.user.guild_permissions.administrator
    has_unblacklist_role = any(
        role.id in {1538119694928842762, 1538120560125415514}
        for role in interaction.user.roles
    )

    if not (is_admin or has_unblacklist_role):
        await interaction.response.send_message(
            "❌ You do not have permission to use this command.", ephemeral=True
        )
        return

    if not is_admin and interaction.channel.name.lower() != "《➥》unblacklist".lower():
        await interaction.response.send_message(
            "❌ You can only use the unblacklist command in the 《➥》unblacklist channel.",
            ephemeral=True,
        )
        return

    clean_id = user.strip("<@!> ")
    if not clean_id.isdigit():
        await interaction.response.send_message(
            "❌ Please provide a valid user or user ID.", ephemeral=True
        )
        return

    target_id = int(clean_id)
    target_id_str = str(target_id)
    if target_id_str not in saved_data_db:
        await interaction.response.send_message(
            f"❌ No blacklist record found for ID `{target_id}`.", ephemeral=True
        )
        return

    guild = interaction.guild
    member = guild.get_member(target_id)
    member_avatar = member.avatar.url if member and member.avatar else None

    # Clean & Stylish Unblacklist Embed Design (Server Restriction Lifted)
    embed = discord.Embed(
        title="✅ Blacklist Revoked",
        description="> A user's server restrictions have been lifted and access has been restored.",
        color=3066993,
    )
    if member_avatar:
        embed.set_thumbnail(url=member_avatar)
    elif guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(
        name="👤 Target User",
        value=f"<@{target_id}>\n`ID: {target_id}`",
        inline=True,
    )
    embed.add_field(
        name="🛡️ Cleared By",
        value=f"{interaction.user.mention}",
        inline=True,
    )
    embed.add_field(
        name="📝 Reason",
        value=f"> {reason}",
        inline=False,
    )
    embed.set_footer(text="TSB Sweden Security System")
    embed.timestamp = datetime.now(timezone.utc)

    view = UnblacklistConfirmView(interaction, target_id)
    await interaction.response.send_message(embed=embed, view=view)
    await view.wait()

    if not view.value:
        return

    data = saved_data_db.pop(target_id_str)
    save_data_file(DATA_FILE, saved_data_db)

    if member:
        blacklist_role = discord.utils.get(guild.roles, name="Blacklisted")
        if blacklist_role:
            await member.remove_roles(blacklist_role)
        if data.get("roles"):
            restored_roles = [
                guild.get_role(r_id)
                for r_id in data["roles"]
                if guild.get_role(r_id)
            ]
            if restored_roles:
                await member.add_roles(*restored_roles)
        try:
            await member.edit(nick=data.get("old_nickname"))
        except Exception:
            pass

        try:
            unbl_text = (
                f"✅ You have been **UNBLACKLISTED** from **{guild.name}**\n\n"
                f"**Reason:** {reason}\n\n"
                "Your roles have been restored. Welcome back!"
            )
            target_user_obj = (
                member
                if isinstance(member, discord.User)
                else await bot.fetch_user(target_id)
            )
            await target_user_obj.send(unbl_text)
        except Exception:
            pass

    log_embed = discord.Embed(
        title="✅ Blacklist Revoked",
        description="> A user's server restrictions have been lifted and access has been restored.",
        color=3066993,
    )
    if member_avatar:
        log_embed.set_thumbnail(url=member_avatar)
    elif guild.icon:
        log_embed.set_thumbnail(url=guild.icon.url)

    log_embed.add_field(
        name="👤 Target User",
        value=f"<@{target_id}>\n`ID: {target_id}`",
        inline=True,
    )
    log_embed.add_field(
        name="🛡️ Cleared By",
        value=f"{interaction.user.mention}",
        inline=True,
    )
    log_embed.add_field(
        name="📝 Reason",
        value=f"> {reason}",
        inline=False,
    )
    log_embed.set_footer(text="TSB Sweden Security System")
    log_embed.timestamp = datetime.now(timezone.utc)

    target_channel = get_target_channel(guild, "《➥》unblacklist")
    if target_channel:
        await target_channel.send(embed=log_embed)
    else:
        await interaction.channel.send(embed=log_embed)


bot.run(os.getenv("TOKEN"))
