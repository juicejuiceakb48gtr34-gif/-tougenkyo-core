import os
import asyncio
import datetime as dt
import math
import re
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "1549679128171778060"))
DB_PATH = os.getenv("DB_PATH", "tougenkyo.db")

ROLE_TEMP = os.getenv("ROLE_TEMP", "TG｜仮住人")
ROLE_MEMBER = os.getenv("ROLE_MEMBER", "TG｜住人")
ROLE_ADMIN = os.getenv("ROLE_ADMIN", "TG｜統括")
DYNAMIC_TRIGGER = os.getenv("DYNAMIC_VC_TRIGGER", "TG｜➕ 個室作成")
LOG_CHANNEL = os.getenv("LOG_CHANNEL", "TG｜運営ログ")

DAILY_REWARD = 20
JOIN_REWARD = 100
GIFT_TAX_RATE = 0.10

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


async def db():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn


async def init_db():
    conn = await db()
    try:
        await conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            coin INTEGER NOT NULL DEFAULT 0,
            cheer INTEGER NOT NULL DEFAULT 0,
            job_xp INTEGER NOT NULL DEFAULT 0,
            last_daily TEXT,
            joined_rewarded INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            gross INTEGER NOT NULL,
            tax INTEGER NOT NULL,
            net INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS applications (
            user_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            reviewed_by INTEGER,
            reviewed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS tickets (
            channel_id INTEGER PRIMARY KEY,
            opener_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dynamic_vc (
            channel_id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT NOT NULL,
            target_id INTEGER,
            detail TEXT,
            created_at TEXT NOT NULL
        );
        """)
        await conn.commit()
    finally:
        await conn.close()


async def ensure_user(user_id: int):
    conn = await db()
    try:
        await conn.execute(
            "INSERT OR IGNORE INTO users(user_id, created_at) VALUES(?,?)",
            (user_id, utcnow().isoformat())
        )
        await conn.commit()
    finally:
        await conn.close()


async def audit(action: str, actor_id: Optional[int] = None, target_id: Optional[int] = None, detail: str = ""):
    conn = await db()
    try:
        await conn.execute(
            "INSERT INTO audit_log(actor_id, action, target_id, detail, created_at) VALUES(?,?,?,?,?)",
            (actor_id, action, target_id, detail[:1000], utcnow().isoformat())
        )
        await conn.commit()
    finally:
        await conn.close()

    guild = bot.get_guild(GUILD_ID)
    if guild:
        ch = discord.utils.get(guild.text_channels, name=LOG_CHANNEL)
        if ch:
            try:
                await ch.send(f"`{action}` actor={actor_id} target={target_id} {detail[:500]}")
            except discord.HTTPException:
                pass


def is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(r.name == ROLE_ADMIN for r in member.roles)


async def get_balance(user_id: int) -> int:
    await ensure_user(user_id)
    conn = await db()
    try:
        row = await (await conn.execute("SELECT coin FROM users WHERE user_id=?", (user_id,))).fetchone()
        return int(row["coin"])
    finally:
        await conn.close()


async def add_coin(user_id: int, amount: int):
    await ensure_user(user_id)
    conn = await db()
    try:
        await conn.execute("UPDATE users SET coin=coin+? WHERE user_id=?", (amount, user_id))
        await conn.commit()
    finally:
        await conn.close()


@bot.event
async def on_ready():
    await init_db()
    guild = bot.get_guild(GUILD_ID)
    if guild:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"[OK] {bot.user} / {guild.name} / commands={len(synced)}")
        except Exception as e:
            print(f"[WARN] command sync failed: {e}")
    else:
        print(f"[WARN] Guild {GUILD_ID} not found")
    await bot.change_presence(activity=discord.Game(name="桃源郷を管理中"))


@bot.tree.command(name="ping", description="きんぱつくんの接続確認")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong {round(bot.latency*1000)}ms", ephemeral=True)


@bot.tree.command(name="balance", description="COIN残高を確認")
async def balance(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target = member or interaction.user
    amount = await get_balance(target.id)
    await interaction.response.send_message(f"🪙 {target.mention} の残高: **{amount} COIN**")


@bot.tree.command(name="daily", description="1日1回のDaily COIN")
async def daily(interaction: discord.Interaction):
    uid = interaction.user.id
    await ensure_user(uid)
    conn = await db()
    try:
        row = await (await conn.execute("SELECT last_daily FROM users WHERE user_id=?", (uid,))).fetchone()
        today = utcnow().date().isoformat()
        if row["last_daily"] == today:
            await interaction.response.send_message("今日はもう受け取っています。", ephemeral=True)
            return
        await conn.execute("UPDATE users SET coin=coin+?, last_daily=? WHERE user_id=?", (DAILY_REWARD, today, uid))
        await conn.commit()
    finally:
        await conn.close()
    await audit("daily", uid, uid, f"+{DAILY_REWARD}")
    await interaction.response.send_message(f"🎁 **{DAILY_REWARD} COIN** を受け取りました。")


@bot.tree.command(name="pay", description="他の住人へCOINを送る（手数料10%）")
@app_commands.describe(member="送金相手", amount="送るCOIN数")
async def pay(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000]):
    if member.bot or member.id == interaction.user.id:
        await interaction.response.send_message("その相手には送金できません。", ephemeral=True)
        return

    sender = interaction.user.id
    receiver = member.id
    bal = await get_balance(sender)
    if bal < amount:
        await interaction.response.send_message("残高が足りません。", ephemeral=True)
        return

    tax = max(1, math.floor(amount * GIFT_TAX_RATE))
    net = amount - tax
    if net <= 0:
        await interaction.response.send_message("送金額が小さすぎます。", ephemeral=True)
        return

    await ensure_user(receiver)
    conn = await db()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        row = await (await conn.execute("SELECT coin FROM users WHERE user_id=?", (sender,))).fetchone()
        if int(row["coin"]) < amount:
            await conn.rollback()
            await interaction.response.send_message("残高が足りません。", ephemeral=True)
            return
        await conn.execute("UPDATE users SET coin=coin-? WHERE user_id=?", (amount, sender))
        await conn.execute("UPDATE users SET coin=coin+? WHERE user_id=?", (net, receiver))
        await conn.execute(
            "INSERT INTO transfers(sender_id,receiver_id,gross,tax,net,created_at) VALUES(?,?,?,?,?,?)",
            (sender, receiver, amount, tax, net, utcnow().isoformat())
        )
        await conn.commit()
    finally:
        await conn.close()

    await audit("pay", sender, receiver, f"gross={amount} tax={tax} net={net}")
    await interaction.response.send_message(
        f"💸 {member.mention} に **{net} COIN** 送金しました。\n"
        f"手数料: **{tax} COIN (10%)**"
    )


@bot.tree.command(name="profile", description="桃源郷プロフィール")
async def profile(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target = member or interaction.user
    await ensure_user(target.id)
    conn = await db()
    try:
        row = await (await conn.execute("SELECT coin,cheer,job_xp FROM users WHERE user_id=?", (target.id,))).fetchone()
    finally:
        await conn.close()
    embed = discord.Embed(title=f"{target.display_name} のプロフィール", color=discord.Color.gold())
    embed.add_field(name="COIN", value=row["coin"])
    embed.add_field(name="CHEER", value=row["cheer"])
    embed.add_field(name="JOB XP", value=row["job_xp"])
    embed.set_thumbnail(url=target.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="apply", description="桃源郷への入郷申請")
@app_commands.describe(reason="参加理由・自己紹介")
async def apply(interaction: discord.Interaction, reason: str):
    uid = interaction.user.id
    await ensure_user(uid)
    conn = await db()
    try:
        await conn.execute(
            """INSERT INTO applications(user_id,status,reason,created_at)
               VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET status=excluded.status, reason=excluded.reason, created_at=excluded.created_at,
               reviewed_by=NULL, reviewed_at=NULL""",
            (uid, "pending", reason[:1000], utcnow().isoformat())
        )
        await conn.commit()
    finally:
        await conn.close()

    member = interaction.user
    if isinstance(member, discord.Member):
        role = discord.utils.get(member.guild.roles, name=ROLE_TEMP)
        if role:
            try:
                await member.add_roles(role, reason="桃源郷 入郷申請")
            except discord.Forbidden:
                pass

    await audit("application_submit", uid, uid, reason[:500])
    await interaction.response.send_message("🌱 入郷申請を受け付けました。運営の確認をお待ちください。", ephemeral=True)


@bot.tree.command(name="approve", description="入郷申請を承認（運営）")
async def approve(interaction: discord.Interaction, member: discord.Member):
    if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
        await interaction.response.send_message("運営専用です。", ephemeral=True)
        return

    temp = discord.utils.get(member.guild.roles, name=ROLE_TEMP)
    official = discord.utils.get(member.guild.roles, name=ROLE_MEMBER)
    try:
        if official:
            await member.add_roles(official, reason="入郷承認")
        if temp:
            await member.remove_roles(temp, reason="正式住人化")
    except discord.Forbidden:
        await interaction.response.send_message("ロール操作権限が足りません。", ephemeral=True)
        return

    await ensure_user(member.id)
    conn = await db()
    try:
        row = await (await conn.execute("SELECT joined_rewarded FROM users WHERE user_id=?", (member.id,))).fetchone()
        reward = 0
        if not row["joined_rewarded"]:
            reward = JOIN_REWARD
            await conn.execute("UPDATE users SET coin=coin+?, joined_rewarded=1 WHERE user_id=?", (reward, member.id))
        await conn.execute(
            """INSERT INTO applications(user_id,status,reason,created_at,reviewed_by,reviewed_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET status='approved', reviewed_by=?, reviewed_at=?""",
            (member.id, "approved", "", utcnow().isoformat(), interaction.user.id, utcnow().isoformat(),
             interaction.user.id, utcnow().isoformat())
        )
        await conn.commit()
    finally:
        await conn.close()

    await audit("application_approve", interaction.user.id, member.id, f"reward={reward}")
    await interaction.response.send_message(f"✅ {member.mention} を正式住人にしました。初回特典 **{reward} COIN**")


@bot.tree.command(name="reject", description="入郷申請を却下（運営）")
async def reject(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
        await interaction.response.send_message("運営専用です。", ephemeral=True)
        return
    conn = await db()
    try:
        await conn.execute(
            """INSERT INTO applications(user_id,status,reason,created_at,reviewed_by,reviewed_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET status='rejected', reviewed_by=?, reviewed_at=?, reason=?""",
            (member.id, "rejected", reason[:1000], utcnow().isoformat(), interaction.user.id, utcnow().isoformat(),
             interaction.user.id, utcnow().isoformat(), reason[:1000])
        )
        await conn.commit()
    finally:
        await conn.close()
    await audit("application_reject", interaction.user.id, member.id, reason[:500])
    await interaction.response.send_message(f"❌ {member.mention} の申請を却下しました。", ephemeral=True)


@bot.tree.command(name="cheer", description="CHEERを付与")
async def cheer(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 100] = 1):
    if member.bot or member.id == interaction.user.id:
        await interaction.response.send_message("その相手にはCHEERできません。", ephemeral=True)
        return
    await ensure_user(member.id)
    conn = await db()
    try:
        await conn.execute("UPDATE users SET cheer=cheer+? WHERE user_id=?", (amount, member.id))
        await conn.commit()
    finally:
        await conn.close()
    await audit("cheer", interaction.user.id, member.id, f"+{amount}")
    await interaction.response.send_message(f"📣 {member.mention} に **{amount} CHEER**")


@bot.tree.command(name="jobxp", description="JOB XPを付与（運営）")
async def jobxp(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 100000]):
    if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
        await interaction.response.send_message("運営専用です。", ephemeral=True)
        return
    await ensure_user(member.id)
    conn = await db()
    try:
        await conn.execute("UPDATE users SET job_xp=job_xp+? WHERE user_id=?", (amount, member.id))
        await conn.commit()
    finally:
        await conn.close()
    await audit("jobxp", interaction.user.id, member.id, f"+{amount}")
    await interaction.response.send_message(f"🧰 {member.mention} に **{amount} JOB XP**")


@bot.tree.command(name="ticket", description="サポートTicketを作成")
async def ticket(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("サーバー内で使ってください。", ephemeral=True)
        return

    safe = re.sub(r"[^a-z0-9-]", "-", interaction.user.name.lower())[:30]
    existing = discord.utils.find(lambda c: c.name == f"ticket-{safe}", guild.text_channels)
    if existing:
        await interaction.response.send_message(f"既にTicketがあります: {existing.mention}", ephemeral=True)
        return

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
    }
    for role in guild.roles:
        if role.name == ROLE_ADMIN:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    channel = await guild.create_text_channel(f"ticket-{safe}", overwrites=overwrites, reason="Ticket作成")
    conn = await db()
    try:
        await conn.execute("INSERT OR REPLACE INTO tickets(channel_id,opener_id,status,created_at) VALUES(?,?,?,?)",
                           (channel.id, interaction.user.id, "open", utcnow().isoformat()))
        await conn.commit()
    finally:
        await conn.close()

    await channel.send(f"{interaction.user.mention} Ticketを作成しました。運営が対応します。")
    await audit("ticket_open", interaction.user.id, channel.id, channel.name)
    await interaction.response.send_message(f"🎫 {channel.mention} を作成しました。", ephemeral=True)


@bot.tree.command(name="close_ticket", description="現在のTicketを閉じる")
async def close_ticket(interaction: discord.Interaction):
    ch = interaction.channel
    if not isinstance(ch, discord.TextChannel) or not ch.name.startswith("ticket-"):
        await interaction.response.send_message("Ticketチャンネルで使ってください。", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        return
    conn = await db()
    try:
        row = await (await conn.execute("SELECT opener_id,status FROM tickets WHERE channel_id=?", (ch.id,))).fetchone()
        if not row:
            await interaction.response.send_message("Ticket情報がありません。", ephemeral=True)
            return
        if interaction.user.id != row["opener_id"] and not is_admin(interaction.user):
            await interaction.response.send_message("このTicketを閉じる権限がありません。", ephemeral=True)
            return
        await conn.execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (ch.id,))
        await conn.commit()
    finally:
        await conn.close()
    await audit("ticket_close", interaction.user.id, ch.id, ch.name)
    await interaction.response.send_message("5秒後にTicketを削除します。")
    await asyncio.sleep(5)
    await ch.delete(reason="Ticket closed")


@bot.tree.command(name="stats", description="桃源郷Core統計（運営）")
async def stats(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
        await interaction.response.send_message("運営専用です。", ephemeral=True)
        return
    conn = await db()
    try:
        users = (await (await conn.execute("SELECT COUNT(*) n FROM users")).fetchone())["n"]
        supply = (await (await conn.execute("SELECT COALESCE(SUM(coin),0) n FROM users")).fetchone())["n"]
        pending = (await (await conn.execute("SELECT COUNT(*) n FROM applications WHERE status='pending'")).fetchone())["n"]
        tickets = (await (await conn.execute("SELECT COUNT(*) n FROM tickets WHERE status='open'")).fetchone())["n"]
    finally:
        await conn.close()
    embed = discord.Embed(title="桃源郷 Core 統計", color=discord.Color.blurple())
    embed.add_field(name="登録ユーザー", value=users)
    embed.add_field(name="COIN供給量", value=supply)
    embed.add_field(name="審査待ち", value=pending)
    embed.add_field(name="未処理Ticket", value=tickets)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    guild = member.guild

    if after.channel and after.channel.name == DYNAMIC_TRIGGER:
        category = after.channel.category
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False),
            member: discord.PermissionOverwrite(connect=True, manage_channels=True, move_members=True),
            guild.me: discord.PermissionOverwrite(connect=True, manage_channels=True, move_members=True)
        }
        for role in guild.roles:
            if role.name == ROLE_MEMBER:
                overwrites[role] = discord.PermissionOverwrite(connect=True)
        new_vc = await guild.create_voice_channel(
            name=f"🔊｜{member.display_name}の部屋",
            category=category,
            overwrites=overwrites,
            reason="動的VC"
        )
        conn = await db()
        try:
            await conn.execute("INSERT OR REPLACE INTO dynamic_vc(channel_id,owner_id,created_at) VALUES(?,?,?)",
                               (new_vc.id, member.id, utcnow().isoformat()))
            await conn.commit()
        finally:
            await conn.close()
        try:
            await member.move_to(new_vc)
        except discord.HTTPException:
            pass
        await audit("dynamic_vc_create", member.id, new_vc.id, new_vc.name)

    if before.channel and before.channel.id != (after.channel.id if after.channel else None):
        conn = await db()
        try:
            row = await (await conn.execute("SELECT owner_id FROM dynamic_vc WHERE channel_id=?", (before.channel.id,))).fetchone()
            if row and len(before.channel.members) == 0:
                await conn.execute("DELETE FROM dynamic_vc WHERE channel_id=?", (before.channel.id,))
                await conn.commit()
                try:
                    await before.channel.delete(reason="空の動的VC")
                except discord.HTTPException:
                    pass
        finally:
            await conn.close()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN が設定されていません。環境変数に設定してください。")
    bot.run(TOKEN)
