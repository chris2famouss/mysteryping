import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask
from threading import Thread
import json
import time
import requests
import random
import os # Import the os module to access environment variables

# --- CONFIG ---
# IMPORTANT: Load your bot token from an environment variable for security.
# NEVER hardcode your token directly in the script for public deployment.
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not TOKEN:
    print("CRITICAL ERROR: DISCORD_BOT_TOKEN environment variable not set.")
    print("Please set the DISCORD_BOT_TOKEN environment variable before running the bot.")
    exit(1) # Exit if the token is not found

WEBHOOK_URL = "https://hook.us2.make.com/7ivckygxl1kybhcq5a7trojybp1abp2f"
COOLDOWN_SECONDS = 3600  # 1 hour

# --- TASKS ---
# This assumes random_tasks.json exists in the same directory.
try:
    with open("random_tasks.json", "r") as f:
        task_list = json.load(f)
except FileNotFoundError:
    print("ERROR: random_tasks.json not found. Please create it.")
    task_list = [] # Initialize as empty to prevent crashes

# --- DISCORD SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True # Ensure this is enabled in your Discord Developer Portal too
bot = commands.Bot(command_prefix="!", intents=intents)

# --- FLASK KEEP ALIVE ---
app = Flask('')

@app.route('/')
def home():
    # UptimeRobot will hit this endpoint. Returning a simple string is enough.
    return "Bot is alive!"

def run():
    # Use the PORT environment variable if available (common in hosting platforms like Heroku/Replit),
    # otherwise default to 8080 (or any other free port).
    port = int(os.environ.get("PORT", 8080))
    print(f"Flask server running on http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    # Start the Flask web server in a separate thread.
    # This prevents the Flask app from blocking your Discord bot's execution.
    Thread(target=run).start()

# --- STORAGE ---
active = {}
scores = {}
cooldowns = {}
streaks = {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f)

def load_json(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except FileNotFoundError: # Handle file not found gracefully
        return {}
    except json.JSONDecodeError: # Handle invalid JSON
        print(f"WARNING: {filename} contains invalid JSON. Starting with empty data.")
        return {}

def save_all():
    save_json("active.json", active)
    save_json("scores.json", scores)
    save_json("cooldowns.json", cooldowns)
    save_json("streaks.json", streaks)

def load_all():
    global active, scores, cooldowns, streaks
    active = load_json("active.json")
    scores = load_json("scores.json")
    cooldowns = load_json("cooldowns.json")
    streaks = load_json("streaks.json")

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await bot.wait_until_ready()
    try:
        # Sync slash commands
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Slash command sync failed: {e}")

    # Set custom presence
    activity = discord.Game(name="Mystery Ping Tasks")
    await bot.change_presence(status=discord.Status.online, activity=activity)

    # Load all data when the bot is ready
    load_all()
    print("Data loaded from JSON files.")

# --- COMMANDS ---
@bot.tree.command(name="gettask", description="Get a random task")
@app_commands.describe(category="Optional category filter")
async def gettask(interaction: discord.Interaction, category: str = None):
    user_id = str(interaction.user.id)
    now = time.time()

    if user_id in cooldowns and now - cooldowns[user_id] < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - cooldowns[user_id]))
        return await interaction.response.send_message(
            f"⏳ Cooldown active. Try again in {remaining // 60}m {remaining % 60}s.", ephemeral=True
        )

    if not task_list:
        return await interaction.response.send_message("❌ No tasks available. Please check `random_tasks.json`.", ephemeral=True)

    filtered = [t for t in task_list if not category or t["category"].lower() == category.lower()]
    if not filtered:
        return await interaction.response.send_message("❌ No tasks in that category.", ephemeral=True)

    task_data = random.choice(filtered)
    active[user_id] = {
        "task": task_data["task"],
        "assigned_at": now,
        "expires_in": 3600
    }
    cooldowns[user_id] = now
    save_all()

    embed = discord.Embed(
        title=f"🧠 {task_data['task']}",
        description=f"Category: **{task_data['category']}**\nDuration: **{task_data['duration']}**",
        color=discord.Color.purple()
    )
    embed.set_footer(text="🎯 Complete this task and use /taskdone")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="taskdone", description="Mark your task as complete")
async def taskdone(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    now = time.time()

    if user_id not in active:
        return await interaction.response.send_message("❌ You have no active task!", ephemeral=True)

    task_info = active[user_id]
    if now > task_info["assigned_at"] + task_info["expires_in"]:
        del active[user_id]
        save_all()
        return await interaction.response.send_message("⚠️ Task expired. Try /gettask again.", ephemeral=True)

    scores.setdefault(user_id, {"xp": 0, "tasks": 0})
    streaks.setdefault(user_id, {"last_day": 0, "count": 0})

    scores[user_id]["xp"] += 10
    scores[user_id]["tasks"] += 1

    today = int(time.time() // 86400)
    if streaks[user_id]["last_day"] == today - 1:
        streaks[user_id]["count"] += 1
    elif streaks[user_id]["last_day"] != today:
        streaks[user_id]["count"] = 1
    streaks[user_id]["last_day"] = today

    bonus = streaks[user_id]["count"]
    scores[user_id]["xp"] += bonus

    del active[user_id]
    save_all()

    level = int((scores[user_id]["xp"] / 10) ** 0.5)

    try:
        requests.post(WEBHOOK_URL, json={
            "discord_id": user_id,
            "username": interaction.user.name,
            "task": task_info["task"],
            "xp": scores[user_id]["xp"],
            "level": level,
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
        })
    except requests.exceptions.RequestException as e: # More specific webhook error handling
        print(f"Webhook error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred with the webhook: {e}")

    await interaction.response.send_message(
        f"✅ Task complete! XP: **{scores[user_id]['xp']}** | Level: **{level}** | 🔥 Streak: {streaks[user_id]['count']} day(s)",
        ephemeral=True
    )

@bot.tree.command(name="leaderboard", description="See the top 10 users by XP")
async def leaderboard(interaction: discord.Interaction):
    if not scores:
        return await interaction.response.send_message("No data yet!", ephemeral=True)

    top = sorted(scores.items(), key=lambda x: x[1]["xp"], reverse=True)[:10]
    embed = discord.Embed(title="🏆 Top XP Earners", color=discord.Color.gold())

    for i, (uid, data) in enumerate(top, start=1):
        # Fetching user objects for display is better practice but might be slow for many users.
        # For simplicity, I'll keep the mention format.
        # If you need username, consider caching or fetching.
        embed.add_field(
            name=f"#{i} • <@{uid}>",
            value=f"XP: **{data['xp']}** | Tasks: {data['tasks']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="dmme", description="Test if the bot can DM you")
async def dmme(interaction: discord.Interaction):
    try:
        await interaction.response.send_message("✅ DMing you...", ephemeral=True)
        await interaction.user.send("👋 This is a test DM from the bot.")
    except discord.Forbidden: # Handle explicit DM blocking
        await interaction.response.send_message("❌ Failed to DM. It seems you have DMs disabled for this server or for the bot.", ephemeral=True)
    except Exception as e: # Catch any other errors
        await interaction.response.send_message(f"❌ Failed to DM. An unknown error occurred: {e}", ephemeral=True)

# --- MAIN ---
if __name__ == "__main__":
    keep_alive() # Start the Flask web server
    bot.run(TOKEN) # Start the Discord bot