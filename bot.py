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
LOGS_FILE = "log_channels.json"


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
log_channels_db = load_data_file(LOGS_FILE)


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


def get_log_channel(guild: discord.Guild, log_type: str):
    guild_id_str = str(guild.id)
    if guild_id_str in log_channels_db and log_type in log_channels_db[guild_id_str]:
        channel_id = log_channels_db[guild_id_str][log_type]
        channel = guild.get_channel(channel_id)
        if channel:
            return channel
    
    default_names = {
        "msg_logs": ["message-logs", "message_logs", "msg-logs", "chat-logs"],
        "mod_logs": ["mod-logs", "mod_logs", "moderation-logs", "staff-logs"]
    }
    for name in default_names.get(log_type, []):
        ch = get_target_channel(guild, name)
        if ch:
            return ch
    return None


async def send_mod_log(guild: discord.Guild, embed: discord.Embed):
    log_channel = get_log_channel(guild, "mod_logs")
    if log_channel:
        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass


def create_record_embed(
    title: str,
    interaction: discord.Interaction,
    target_mention: str,
    target_id: int,
    reason: str,
    category: str = None,
    avatar_url: str = None,
    extra_fields: list = None,
):
    embed = discord.Embed(
        title=f"⛔ {title}",
        description="> **A security enforcement action has been successfully processed.**",
        color=discord.Color.from_rgb(220, 20, 60),
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    elif interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    embed.add_field(
        name="🎯 Target User",
        value=f"{target_mention}\n`ID: {target_id}`",
        inline=True,
    )
    embed.add_field(
        name="👤 Moderator", value=f"{interaction.user.mention}", inline=True
    )

    if category:
        embed.add_field(name="📂 Category", value=f"**{category}**", inline=True)

    embed.add_field(name="📝 Reason", value=reason, inline=False)

    if extra_fields:
        for name, value, inline in extra_fields:
            embed.add_field(name=name, value=value, inline=inline)

    embed.set_footer(
        text=f"Server ID: {interaction.guild.id}",
        icon_url=interaction.guild.icon.url if interaction.guild.icon else None,
    )
    return embed


class BlacklistConfirmView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, target_id: int):
        super().__init__(timeout=60)
        self.orig_interaction = interaction
        self.target_id = target_id
        self.value = None

    @discord.ui.button(label="Confirm Blacklist", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.orig_interaction.user:
            await interaction.response.send_message("❌ Only the moderator who initiated this command can confirm it.", ephemeral=True)
            return
        await interaction.response.defer()
        self.value = True
        self.stop()
        try:
            await interaction.message.delete()
        except Exception:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.orig_interaction.user:
            await interaction.response.send_message("❌ Only the moderator who initiated this command can cancel it.", ephemeral=True)
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

    @discord.ui.button(label="Confirm Unblacklist", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.orig_interaction.user:
            await interaction.response.send_message("❌ Only the moderator who initiated this command can confirm it.", ephemeral=True)
            return
        await interaction.response.defer()
        self.value = True
        self.stop()
        try:
            await interaction.message.delete()
        except Exception:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.orig_interaction.user:
            await interaction.response.send_message("❌ Only the moderator who initiated this command can cancel it.", ephemeral=True)
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
    
    # Instant command sync directly to all joined servers
    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Instantly synced {len(synced)} commands to server: {guild.name}")
        except Exception as e:
            print(f"Failed to sync to {guild.name}: {e}")

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


# ==================== AUTOMATED MESSAGE LOGS ====================

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    log_channel = get_log_channel(message.guild, "msg_logs")
    if not log_channel or log_channel.id == message.channel.id:
        return

    embed = discord.Embed(
        title="🗑️ Message Deleted",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Author", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
    embed.add_field(name="Content", value=message.content or "*No text content*", inline=False)

    if message.attachments:
        files_info = "\n".join([f"[{a.filename}]({a.url})" for a in message.attachments])
        embed.add_field(name="Attachments", value=files_info, inline=False)

    embed.set_footer(text=f"Message ID: {message.id}")
    await log_channel.send(embed=embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content:
        return

    log_channel = get_log_channel(before.guild, "msg_logs")
    if not log_channel:
        return

    embed = discord.Embed(
        title="✏️ Message Edited",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Author", value=f"{before.author.mention} (`{before.author.id}`)", inline=True)
    embed.add_field(name="Channel", value=before.channel.mention, inline=True)
    embed.add_field(name="Before", value=before.content or "*Empty*", inline=False)
    embed.add_field(name="After", value=after.content or "*Empty*", inline=False)
    embed.add_field(name="Jump Link", value=f"[Go to Message]({after.jump_url})", inline=False)

    embed.set_footer(text=f"Message ID: {before.id}")
    await log_channel.send(embed=embed)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    embed = discord.Embed(
        title="🔨 Member Banned",
        color=discord.Color.dark_red(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Target User", value=f"{user.mention} (`{user.id}`)", inline=False)
    await send_mod_log(guild, embed)


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    embed = discord.Embed(
        title="🔓 Member Unbanned",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Target User", value=f"{user.mention} (`{user.id}`)", inline=False)
    await send_mod_log(guild, embed)


# ==================== LOGGING CONFIG COMMAND ====================

@bot.tree.command(name="setlogchannel", description="Set log channels for moderation or message events")
@app_commands.describe(log_type="The type of logs to configure", channel="The destination channel")
@app_commands.choices(
    log_type=[
        app_commands.Choice(name="Message Logs", value="msg_logs"),
        app_commands.Choice(name="Mod Logs", value="mod_logs"),
    ]
)
async def setlogchannel(interaction: discord.Interaction, log_type: str, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin permissions required.", ephemeral=True)
        return

    guild_id_str = str(interaction.guild.id)
    if guild_id_str not in log_channels_db:
        log_channels_db[guild_id_str] = {}

    log_channels_db[guild_id_str][log_type] = channel.id
    save_data_file(LOGS_FILE, log_channels_db)

    await interaction.response.send_message(
        f"✅ Updated **{log_type.replace('_', ' ').title()}** target to {channel.mention}.", ephemeral=True
    )


# ==================== MODERATION COMMANDS ====================

@bot.tree.command(name="timeout", description="Timeout a member")
@app_commands.describe(
    member="The member to timeout",
    duration="Duration (e.g. 10m, 1h, 1d)",
    reason="Reason for the timeout",
)
async def timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    duration: str,
    reason: str,
):
    if not has_custom_role_or_admin(interaction):
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    if not check_hierarchy(interaction, member):
        await interaction.response.send_message("You cannot timeout this member due to having an equal or higher role then you", ephemeral=True)
        return

    seconds = 0
    unit = duration[-1].lower()
    val = duration[:-1]
    if not val.isdigit():
        await interaction.response.send_message("❌ Invalid duration format! Use numbers followed by s, m, h, or d (e.g., 10m).", ephemeral=True)
        return
    num = int(val)
    if unit == "s":
        seconds = num
    elif unit == "m":
        seconds = num * 60
    elif unit == "h":
        seconds = num * 3600
    elif unit == "d":
        seconds = num * 86400
    else:
        await interaction.response.send_message("❌ Invalid unit! Use s, m, h, or d.", ephemeral=True)
        return

    delta = timedelta(seconds=seconds)
    try:
        await member.timeout(delta, reason=reason)
        avatar_url = member.avatar.url if member.avatar else None
        embed = create_record_embed(
            title="USER TIMEOUT RECORD",
            interaction=interaction,
            target_mention=member.mention,
            target_id=member.id,
            reason=reason,
            avatar_url=avatar_url,
            extra_fields=[("⏳ Duration", f"`{duration}`", True)],
        )
        await interaction.response.send_message(embed=embed)
        await send_mod_log(interaction.guild, embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to timeout member: {e}", ephemeral=True)


@bot.tree.command(name="warn", description="Warn a member")
@app_commands.describe(
    user="The user to warn (User or User ID)",
    category="Category of the violation",
    severity="Severity of the warn",
    reason="Reason for the warning",
)
@app_commands.choices(
    severity=[
        app_commands.Choice(name="Minor warning", value="Minor warning"),
        app_commands.Choice(name="Moderate warning", value="Moderate warning"),
        app_commands.Choice(name="Severe warning", value="Severe warning"),
        app_commands.Choice(name="Critical warning", value="Critical warning"),
    ]
)
@app_commands.choices(
    category=[
        app_commands.Choice(name="Harassment", value="Harassment"),
        app_commands.Choice(name="NSFW", value="NSFW"),
        app_commands.Choice(name="Spam", value="Spam"),
        app_commands.Choice(name="Advertising", value="Advertising"),
        app_commands.Choice(name="Off-Topic", value="Off-Topic"),
        app_commands.Choice(name="Hate Speech", value="Hate Speech"),
        app_commands.Choice(name="Language", value="Language"),
        app_commands.Choice(name="Privacy", value="Privacy"),
        app_commands.Choice(name="ToS Violation", value="ToS Violation"),
        app_commands.Choice(name="Staff Disrespect", value="Staff Disrespect"),
        app_commands.Choice(name="Impersonation", value="Impersonation"),
        app_commands.Choice(name="Toxicity", value="Toxicity"),
        app_commands.Choice(name="Defamation", value="Defamation"),
    ]
)
async def warn(
    interaction: discord.Interaction,
    user: str,
    category: str,
    severity: str,
    reason: str,
):
    if not has_custom_role_or_admin(interaction):
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    clean_id = user.strip("<@!> ")
    if not clean_id.isdigit():
        await interaction.response.send_message("❌ Please provide a valid user or user ID.", ephemeral=True)
        return

    user_id = int(clean_id)
    target_member = interaction.guild.get_member(user_id)
    if target_member and not check_hierarchy(interaction, target_member):
        await interaction.response.send_message("You cannot warn this member due to having an equal or higher role then you", ephemeral=True)
        return

    clean_expired_warns()
    user_id_str = str(user_id)

    if user_id_str not in warns_db:
        warns_db[user_id_str] = []

    now = datetime.now(timezone.utc)
    expires_at = None

    if severity == "Minor warning":
        expires_at = (now + timedelta(days=15)).timestamp()
        sev_display = "Minor warning"
    elif severity == "Moderate warning":
        expires_at = (now + timedelta(days=30)).timestamp()
        sev_display = "Moderate warning"
    elif severity == "Severe warning":
        expires_at = (now + timedelta(days=40)).timestamp()
        sev_display = "Severe warning"
    elif severity == "Critical warning":
        expires_at = None
        sev_display = "Critical warning"

    warn_id = random.randint(1000, 9999)
    warn_entry = {
        "id": warn_id,
        "category": category,
        "severity": sev_display,
        "reason": reason,
        "moderator": interaction.user.id,
        "timestamp": int(now.timestamp()),
        "expires_at": expires_at,
    }
    warns_db[user_id_str].append(warn_entry)
    save_data_file(WARNS_FILE, warns_db)

    active_count = len(warns_db[user_id_str])
    avatar_url = target_member.avatar.url if target_member and target_member.avatar else None
    issued_unix = int(now.timestamp())

    embed = create_record_embed(
        title="USER WARNING RECORD",
        interaction=interaction,
        target_mention=f"<@{user_id}>",
        target_id=user_id,
        reason=reason,
        category=category,
        avatar_url=avatar_url,
        extra_fields=[
            ("⚠️ Severity", f"`{sev_display}`", True),
            ("⏳ Expires", f"<t:{issued_unix if expires_at is None else int(expires_at)}:R>", True),
            ("🔸 Status", f"Strike `{active_count}` of `5` max", True),
        ],
    )
    await interaction.response.send_message(embed=embed)
    await send_mod_log(interaction.guild, embed)


@bot.tree.command(name="warnings", description="Check active warnings for a user")
@app_commands.describe(user="The user or user ID to check")
async def warnings(interaction: discord.Interaction, user: str):
    if not has_custom_role_or_admin(interaction):
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    clean_id = user.strip("<@!> ")
    user_id = int(clean_id)
    user_warns = warns_db.get(str(user_id), [])
    target_member = interaction.guild.get_member(user_id)
    avatar_url = target_member.avatar.url if target_member and target_member.avatar else None

    embed = discord.Embed(
        title="🛡️ TSB SWEDEN — ACTIVE DOSSIER",
        description=f"> **Record Status:** `{len(user_warns)} of 5 active strikes`",
        color=discord.Color.from_rgb(220, 20, 60),
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    embed.add_field(name="🎯 Target User", value=f"<@{user_id}>\n`ID: {user_id}`", inline=False)
    for idx, w in enumerate(user_warns, 1):
        embed.add_field(
            name=f"Infraction [{idx}] ({w.get('severity')})",
            value=f"**Violation:** {w.get('reason')}\n**Category:** `{w.get('category')}`",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="removewarn", description="Remove a specific warning index from a user")
@app_commands.describe(user="The user or user ID", warn_index="The warning number to remove")
async def removewarn(interaction: discord.Interaction, user: str, warn_index: int):
    if not has_custom_role_or_admin(interaction):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return

    clean_id = user.strip("<@!> ")
    if not clean_id.isdigit():
        await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)
        return
    user_id = int(clean_id)
    user_id_str = str(user_id)

    if user_id_str not in warns_db or not warns_db[user_id_str]:
        await interaction.response.send_message("❌ This user has no active warnings to remove.", ephemeral=True)
        return

    user_warns = warns_db[user_id_str]
    if 1 <= warn_index <= len(user_warns):
        removed = user_warns.pop(warn_index - 1)
        if not user_warns:
            del warns_db[user_id_str]
        save_data_file(WARNS_FILE, warns_db)
        await interaction.response.send_message(
            f"✅ Successfully removed warning `#{warn_index}` (**{removed.get('reason')}**) for <@{user_id}>.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(f"❌ Invalid warning index. Choose between `1` and `{len(user_warns)}`.", ephemeral=True)


@bot.tree.command(name="blacklist", description="Blacklist a member by user or user ID")
@app_commands.describe(user="The member or user ID to blacklist", reason="Reason", category="Category")
async def blacklist(
    interaction: discord.Interaction,
    user: str,
    reason: str,
    category: Literal["Appealable⚖️", "Bail only💰", "Permanent⛔"],
):
    is_admin = interaction.user.guild_permissions.administrator
    has_blacklist_role = any(role.id == 1538119694928842762 for role in interaction.user.roles)

    if not (is_admin or has_blacklist_role):
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    if not is_admin and interaction.channel.name.lower() != "《➦》blacklist".lower():
        await interaction.response.send_message("❌ You can only use the blacklist command in the 《➦》blacklist channel.", ephemeral=True)
        return

    clean_id = user.strip("<@!> ")
    if not clean_id.isdigit():
        await interaction.response.send_message("❌ Please provide a valid user or user ID.", ephemeral=True)
        return

    target_id = int(clean_id)
    guild = interaction.guild
    member = guild.get_member(target_id)

    if member and not check_hierarchy(interaction, member):
        await interaction.response.send_message("You cannot blacklist this member due to having an equal or higher role then yours", ephemeral=True)
        return

    member_avatar = member.avatar.url if member and member.avatar else None

    embed = discord.Embed(
        title="🚫 User Blacklisted",
        description="> A user has been restricted from the server.",
        color=16730183,
    )
    if member_avatar:
        embed.set_thumbnail(url=member_avatar)
    elif guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(name="👤 Target User", value=f"<@{target_id}>\n`ID: {target_id}`", inline=True)
    embed.add_field(name="🛡️ Moderated By", value=f"{interaction.user.mention}", inline=True)
    embed.add_field(name="📂 Category", value=f"**{category}**", inline=False)
    embed.add_field(name="📝 Reason", value=f"> {reason}", inline=False)
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
            await member.remove_roles(*(guild.get_role(r) for r in role_ids if guild.get_role(r)))
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

            target_user_obj = member if isinstance(member, discord.User) else await bot.fetch_user(target_id)
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

    log_embed.add_field(name="👤 Target User", value=f"<@{target_id}>\n`ID: {target_id}`", inline=True)
    log_embed.add_field(name="🛡️ Moderated By", value=f"{interaction.user.mention}", inline=True)
    log_embed.add_field(name="📂 Category", value=f"**{category}**", inline=False)
    log_embed.add_field(name="📝 Reason", value=f"> {reason}", inline=False)
    log_embed.set_footer(text="TSB Sweden Security System")
    log_embed.timestamp = datetime.now(timezone.utc)

    target_channel = get_target_channel(guild, "《➦》blacklist")
    if target_channel:
        await target_channel.send(embed=log_embed)
    else:
        await interaction.channel.send(embed=log_embed)

    await send_mod_log(guild, log_embed)


@bot.tree.command(name="unblacklist", description="Remove a user from the blacklist")
@app_commands.describe(user="User mention or User ID to unblacklist", reason="Reason")
async def unblacklist(interaction: discord.Interaction, user: str, reason: str):
    is_admin = interaction.user.guild_permissions.administrator
    has_unblacklist_role = any(
        role.id in {1538119694928842762, 1538120560125415514}
        for role in interaction.user.roles
    )

    if not (is_admin or has_unblacklist_role):
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    if not is_admin and interaction.channel.name.lower() != "《➥》unblacklist".lower():
        await interaction.response.send_message("❌ You can only use the unblacklist command in the 《➥》unblacklist channel.", ephemeral=True)
        return

    clean_id = user.strip("<@!> ")
    if not clean_id.isdigit():
        await interaction.response.send_message("❌ Please provide a valid user or user ID.", ephemeral=True)
        return

    target_id = int(clean_id)
    target_id_str = str(target_id)
    if target_id_str not in saved_data_db:
        await interaction.response.send_message(f"❌ No blacklist record found for ID `{target_id}`.", ephemeral=True)
        return

    guild = interaction.guild
    member = guild.get_member(target_id)
    member_avatar = member.avatar.url if member and member.avatar else None

    embed = discord.Embed(
        title="✅ Blacklist Revoked",
        description="> A user's server restrictions have been lifted and access has been restored.",
        color=3066993,
    )
    if member_avatar:
        embed.set_thumbnail(url=member_avatar)
    elif guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(name="👤 Target User", value=f"<@{target_id}>\n`ID: {target_id}`", inline=True)
    embed.add_field(name="🛡️ Cleared By", value=f"{interaction.user.mention}", inline=True)
    embed.add_field(name="📝 Reason", value=f"> {reason}", inline=False)
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
            restored_roles = [guild.get_role(r_id) for r_id in data["roles"] if guild.get_role(r_id)]
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
            target_user_obj = member if isinstance(member, discord.User) else await bot.fetch_user(target_id)
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

    log_embed.add_field(name="👤 Target User", value=f"<@{target_id}>\n`ID: {target_id}`", inline=True)
    log_embed.add_field(name="🛡️ Cleared By", value=f"{interaction.user.mention}", inline=True)
    log_embed.add_field(name="📝 Reason", value=f"> {reason}", inline=False)
    log_embed.set_footer(text="TSB Sweden Security System")
    log_embed.timestamp = datetime.now(timezone.utc)

    target_channel = get_target_channel(guild, "《➥》unblacklist")
    if target_channel:
        await target_channel.send(embed=log_embed)
    else:
        await interaction.channel.send(embed=log_embed)

    await send_mod_log(guild, log_embed)


@bot.tree.command(name="viewblacklistinfo", description="View active blacklist details for a user")
@app_commands.describe(user="The user or user ID to check")
async def viewblacklistinfo(interaction: discord.Interaction, user: str):
    if not has_custom_role_or_admin(interaction):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return

    clean_id = user.strip("<@!> ")
    if not clean_id.isdigit():
        await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)
        return
    user_id_str = str(clean_id)

    if user_id_str not in saved_data_db:
        await interaction.response.send_message(f"❌ User with ID `{clean_id}` is not currently blacklisted.", ephemeral=True)
        return

    data = saved_data_db[user_id_str]
    target_member = interaction.guild.get_member(int(clean_id))
    avatar_url = target_member.avatar.url if target_member and target_member.avatar else None

    embed = discord.Embed(
        title="🛡️ BLACKLIST DOSSIER INFO",
        color=discord.Color.from_rgb(220, 20, 60),
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    embed.add_field(name="🎯 Target User", value=f"<@{clean_id}>\n`ID: {clean_id}`", inline=False)
    embed.add_field(name="📂 Category", value=f"**{data.get('category', 'N/A')}**", inline=True)
    embed.add_field(name="📝 Reason", value=data.get("reason", "No reason provided"), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="send", description="Send a custom message or announcement to a channel")
@app_commands.describe(channel="Channel to send the message to", message="The message content")
async def send(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    if not has_custom_role_or_admin(interaction):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return

    try:
        await channel.send(message)
        await interaction.response.send_message(f"✅ Successfully sent message to {channel.mention}.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to send message: {e}", ephemeral=True)


@bot.tree.command(name="giverank", description="Give or remove rank roles")
async def giverank(interaction: discord.Interaction, user: discord.Member, action: str):
    if not has_custom_role_or_admin(interaction):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    await interaction.response.send_message(f"✅ Done for {user.mention}.", ephemeral=True)


bot.run(os.getenv("TOKEN"))
