import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask
from threading import Thread
import json
import time
import requests
import random
import os
import psycopg2

# --- CONFIG ---
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not TOKEN:
    print("CRITICAL ERROR: DISCORD_BOT_TOKEN environment variable not set.")
    exit(1)

if not DATABASE_URL:
    print("CRITICAL ERROR: DATABASE_URL environment variable not set.")
    exit(1)

WEBHOOK_URL = "https://hook.us2.make.com/7ivckygxl1kybhcq5a7trojybp1abp2f"
COOLDOWN_SECONDS = 3600

active = {}

# --- TASKS ---
try:
    with open("random_tasks.json", "r") as f:
        task_list = [task for task in json.load(f) if isinstance(task, dict)]
except FileNotFoundError:
    print("ERROR: random_tasks.json not found. Please create it.")
    task_list = []

# --- DISCORD SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- FLASK KEEP ALIVE ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    port = int(os.environ.get("PORT", 8080))
    print(f"Flask server running on http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run).start()

# --- DATABASE FUNCTIONS ---
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

def setup_db():
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_tasks (
                user_id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                category TEXT,
                duration TEXT,
                assigned_at BIGINT NOT NULL,
                expires_in INT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_data (
                user_id TEXT PRIMARY KEY,
                xp INTEGER DEFAULT 0,
                tasks_completed INTEGER DEFAULT 0,
                last_cooldown BIGINT DEFAULT 0,
                streak_last_day INTEGER DEFAULT 0,
                streak_count INTEGER DEFAULT 0
            );
        """)
        conn.commit()
        cursor.close()
        conn.close()
        print("Database tables checked/created successfully.")
    else:
        print("Could not connect to database for setup.")

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await bot.wait_until_ready()
    setup_db()
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Slash command sync failed: {e}")

    activity = discord.Game(name="Mystery Ping Tasks")
    await bot.change_presence(status=discord.Status.online, activity=activity)

# --- COMMANDS ---
import discord
from discord.ext import commands
from discord import app_commands
import json
import time
import random

# --- GLOBALS ---
active = {}

# --- COMMAND ---
@bot.tree.command(name="gettask", description="Sends you a random task via DM.")
async def gettask(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    user_id = interaction.user.id

    try:
        # Load tasks from the JSON file
        with open("random_tasks.json", "r") as f:
            tasks = json.load(f)

        # Handle if empty
        if not tasks:
            await interaction.followup.send("❌ No tasks available. Please check `random_tasks.json`.", ephemeral=True)
            return

        # Pick a random task
        task = random.choice(tasks)

        # Create the task embed
        embed = discord.Embed(
            title="🎯 Your Task",
            description=task,
            color=discord.Color.green()
        )

        # DM the user
        await interaction.user.send(embed=embed)

        # Save active task state
        active[user_id] = {
            "task": task,
            "timestamp": time.time(),
            "status": "waiting_for_completion"
        }

        await interaction.followup.send("📩 Task sent to your DMs!", ephemeral=True)

    except discord.Forbidden:
        await interaction.followup.send("❌ I couldn't DM you. Please open your DMs and try again.", ephemeral=True)

    except FileNotFoundError:
        await interaction.followup.send("❌ Task file not found. Make sure `random_tasks.json` exists.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ An unexpected error occurred: `{str(e)}`", ephemeral=True)


@bot.tree.command(name="taskdone", description="Mark your task as complete")
async def taskdone(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    now = int(time.time())
    today = int(now // 86400) # Day number for streak calculation

    conn = get_db_connection()
    if not conn:
        return await interaction.response.send_message("❌ Database error. Please try again later.", ephemeral=True)

    try:
        cursor = conn.cursor()

        # Fetch active task
        cursor.execute("SELECT task, category, duration, assigned_at, expires_in FROM active_tasks WHERE user_id = %s", (user_id,))
        task_info = cursor.fetchone()

        if not task_info:
            return await interaction.response.send_message("❌ You have no active task!", ephemeral=True)

        task_name, task_category, task_duration, assigned_at, expires_in = task_info

        if now > assigned_at + expires_in:
            cursor.execute("DELETE FROM active_tasks WHERE user_id = %s", (user_id,))
            conn.commit()
            return await interaction.response.send_message("⚠️ Task expired. Try /gettask again.", ephemeral=True)

        # Fetch or initialize user data
        cursor.execute("SELECT xp, tasks_completed, streak_last_day, streak_count FROM user_data WHERE user_id = %s", (user_id,))
        user_data_row = cursor.fetchone()

        current_xp = user_data_row[0] if user_data_row else 0
        tasks_completed = user_data_row[1] if user_data_row else 0
        streak_last_day = user_data_row[2] if user_data_row else 0
        streak_count = user_data_row[3] if user_data_row else 0

        current_xp += 10
        tasks_completed += 1

        # Calculate streak bonus
        if streak_last_day == today - 1: # Completed yesterday
            streak_count += 1
        elif streak_last_day != today: # Not completed yesterday or today
            streak_count = 1
        # If streak_last_day == today, means they already completed a task today,
        # so we don't increase the streak count for this specific task.
        # This prevents inflating the streak with multiple tasks in one day.

        bonus_xp = streak_count # Bonus XP equals current streak count
        current_xp += bonus_xp

        # Update user data in the database
        cursor.execute("""
            INSERT INTO user_data (user_id, xp, tasks_completed, streak_last_day, streak_count)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                xp = EXCLUDED.xp,
                tasks_completed = EXCLUDED.tasks_completed,
                streak_last_day = EXCLUDED.streak_last_day,
                streak_count = EXCLUDED.streak_count;
        """, (user_id, current_xp, tasks_completed, today, streak_count))

        # Delete active task
        cursor.execute("DELETE FROM active_tasks WHERE user_id = %s", (user_id,))
        conn.commit()

        level = int((current_xp / 10) ** 0.5) # Re-calculate level based on new XP

        try:
            requests.post(WEBHOOK_URL, json={
                "discord_id": user_id,
                "username": interaction.user.name,
                "task": task_name,
                "xp": current_xp,
                "level": level,
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
            })
        except requests.exceptions.RequestException as e:
            print(f"Webhook error: {e}")
        except Exception as e:
            print(f"An unexpected error occurred with the webhook: {e}")

        await interaction.response.send_message(
            f"✅ Task complete! XP: **{current_xp}** | Level: **{level}** | 🔥 Streak: {streak_count} day(s)",
            ephemeral=True
        )

    except Exception as e:
        print(f"Error in /taskdone: {e}")
        await interaction.response.send_message("❌ An error occurred while completing your task. Please try again.", ephemeral=True)
    finally:
        if conn:
            conn.close()

@bot.tree.command(name="leaderboard", description="See the top 10 users by XP")
async def leaderboard(interaction: discord.Interaction):
    conn = get_db_connection()
    if not conn:
        return await interaction.response.send_message("❌ Database error. Please try again later.", ephemeral=True)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, xp, tasks_completed FROM user_data ORDER BY xp DESC LIMIT 10")
        top_users = cursor.fetchall()

        if not top_users:
            return await interaction.response.send_message("No data yet! Be the first to earn XP!", ephemeral=True)

        embed = discord.Embed(title="🏆 Top XP Earners", color=discord.Color.gold())

        for i, (uid, xp, tasks) in enumerate(top_users, start=1):
            embed.add_field(
                name=f"#{i} • <@{uid}>", # Using mention format, Discord will resolve to username
                value=f"XP: **{xp}** | Tasks: {tasks}",
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    except Exception as e:
        print(f"Error in /leaderboard: {e}")
        await interaction.response.send_message("❌ An error occurred while fetching the leaderboard. Please try again.", ephemeral=True)
    finally:
        if conn:
            conn.close()

@bot.tree.command(name="dmme", description="Test if the bot can DM you")
async def dmme(interaction: discord.Interaction):
    try:
        await interaction.response.send_message("✅ DMing you...", ephemeral=True)
        await interaction.user.send("👋 This is a test DM from the bot.")
    except discord.Forbidden:
        await interaction.response.send_message("❌ Failed to DM. It seems you have DMs disabled for this server or for the bot.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to DM. An unknown error occurred: {e}", ephemeral=True)

if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
