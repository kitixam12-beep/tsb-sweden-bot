# Place these log configuration utilities near your file/database helpers:
LOGS_FILE = "log_channels.json"
log_channels_db = load_data_file(LOGS_FILE)

def get_log_channel(guild: discord.Guild, log_type: str):
    guild_id_str = str(guild.id)
    if guild_id_str in log_channels_db and log_type in log_channels_db[guild_id_str]:
        channel_id = log_channels_db[guild_id_str][log_type]
        return guild.get_channel(channel_id)
    
    # Fallback to default channel search if not explicitly set
    default_names = {
        "msg_logs": ["message-logs", "msg-logs", "chat-logs"],
        "mod_logs": ["mod-logs", "moderation-logs", "staff-logs"]
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

# ==================== LOGGING COMMANDS ====================

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

# ==================== EVENT LISTENERS ====================

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
