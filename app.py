import os
import asyncio
import threading
import random
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify
import discord
from discord.ext import tasks
from pymongo import MongoClient

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

# ========== MONGO DB ==========
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://wastedrus655_db_user:55Fxvg7t@cluster0.mqvx176.mongodb.net/?appName=Cluster0")
client = MongoClient(MONGO_URI)
db = client["robycord"]
tokens_col = db["tokens"]

# ========== DISCORD CLIENTS ==========
discord_clients = {}
message_queues = {}

config = {
    "delay_min": 5.0,
    "delay_max": 10.0,
    "current_status": "dnd",
}

STATUS_MAP = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "offline": discord.Status.invisible
}

class MySelfBot(discord.Client):
    def __init__(self, token_id, token, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token_id = token_id
        self.token = token

    async def on_ready(self):
        print(f'[Self-Bot] {self.user} conectat')
        await self.change_presence(status=STATUS_MAP.get(config["current_status"], discord.Status.online))
        
        tokens_col.update_one(
            {"_id": self.token_id},
            {"$set": {"username": str(self.user), "status": "online", "last_seen": datetime.now()}}
        )
        
        self.process_queue.start()

    @tasks.loop(seconds=0.1)
    async def process_queue(self):
        queue = message_queues.get(self.token_id)
        if queue is None or queue.empty():
            return

        item = await queue.get()
        
        channel_id = int(item["channel_id"])
        user_mention = item.get("user_mention", "")
        message_text = item["message"]
        should_type = item.get("simulate_typing", False)
        
        channel = self.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(channel_id)
            except:
                queue.task_done()
                return

        final_message = message_text
        if user_mention and user_mention.strip():
            clean = user_mention.replace("<", "").replace("@", "").replace(">", "").strip()
            final_message = f"<@{clean}> {final_message}"

        try:
            if should_type:
                async with channel.typing():
                    await asyncio.sleep(2.5)
            await channel.send(final_message)
            print(f"[{self.user}] Trimis")
        except Exception as e:
            print(f"[Eroare] {e}")

        sleep_time = random.uniform(config["delay_min"], config["delay_max"])
        await asyncio.sleep(sleep_time)
        queue.task_done()

# ========== FUNCȚII TOKEN ==========
def load_tokens():
    return list(tokens_col.find())

def start_bot_for_token(token_data):
    token_id = token_data["_id"]
    token = token_data["token"]
    
    if token_id in discord_clients:
        return
    
    loop = asyncio.new_event_loop()
    
    def run():
        asyncio.set_event_loop(loop)
        queue = asyncio.Queue()
        message_queues[token_id] = queue
        client = MySelfBot(token_id, token)
        discord_clients[token_id] = client
        loop.run_until_complete(client.start(token))
    
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    print(f"Bot pornit pentru token {token_id}")

def start_all_bots():
    for token_data in load_tokens():
        start_bot_for_token(token_data)

# ========== RUTE API ==========
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/tokens", methods=["GET"])
def get_tokens():
    tokens = load_tokens()
    result = []
    for t in tokens:
        result.append({
            "id": str(t["_id"]),
            "username": t.get("username", "Unknown"),
            "status": t.get("status", "offline"),
            "token_preview": t["token"][:15] + "..." if len(t["token"]) > 15 else t["token"]
        })
    return jsonify(result)

@app.route("/api/tokens/add", methods=["POST"])
def add_token():
    data = request.json or {}
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"error": "Token required"}), 400
    
    existing = tokens_col.find_one({"token": token})
    if existing:
        return jsonify({"error": "Token already exists"}), 400
    
    token_data = {
        "token": token,
        "username": "Unknown",
        "status": "offline",
        "added_at": datetime.now(),
        "last_seen": None
    }
    result = tokens_col.insert_one(token_data)
    return jsonify({"success": True, "id": str(result.inserted_id)})

@app.route("/api/tokens/<token_id>/start", methods=["POST"])
def start_token(token_id):
    from bson import ObjectId
    token_data = tokens_col.find_one({"_id": ObjectId(token_id)})
    if not token_data:
        return jsonify({"error": "Token not found"}), 404
    start_bot_for_token(token_data)
    return jsonify({"success": True})

@app.route("/api/tokens/<token_id>/stop", methods=["POST"])
def stop_token(token_id):
    if token_id in discord_clients:
        client = discord_clients[token_id]
        asyncio.run_coroutine_threadsafe(client.close(), client.loop)
        del discord_clients[token_id]
        if token_id in message_queues:
            del message_queues[token_id]
        
        from bson import ObjectId
        tokens_col.update_one({"_id": ObjectId(token_id)}, {"$set": {"status": "offline"}})
        return jsonify({"success": True})
    return jsonify({"error": "Not running"}), 404

@app.route("/api/tokens/<token_id>/delete", methods=["POST"])
def delete_token(token_id):
    if token_id in discord_clients:
        client = discord_clients[token_id]
        asyncio.run_coroutine_threadsafe(client.close(), client.loop)
        del discord_clients[token_id]
        if token_id in message_queues:
            del message_queues[token_id]
    
    from bson import ObjectId
    tokens_col.delete_one({"_id": ObjectId(token_id)})
    return jsonify({"success": True})

@app.route("/api/tokens/<token_id>/send", methods=["POST"])
def send_message(token_id):
    data = request.json or {}
    channel_id = data.get("channel_id")
    message = data.get("message")
    user_mention = data.get("user_mention", "")
    simulate_typing = data.get("simulate_typing", False)
    
    if not channel_id or not message:
        return jsonify({"error": "Missing fields"}), 400
    
    queue = message_queues.get(token_id)
    if queue is None:
        return jsonify({"error": "Bot not running"}), 400
    
    asyncio.run_coroutine_threadsafe(
        queue.put({
            "channel_id": channel_id,
            "user_mention": user_mention,
            "message": message,
            "simulate_typing": simulate_typing
        }),
        discord_clients[token_id].loop
    )
    
    return jsonify({"success": True, "queue_count": queue.qsize()})

@app.route("/api/tokens/<token_id>/queue", methods=["GET"])
def get_queue(token_id):
    queue = message_queues.get(token_id)
    q_size = queue.qsize() if queue else 0
    return jsonify({"queue_count": q_size})

@app.route("/api/tokens/<token_id>/clear", methods=["POST"])
def clear_queue(token_id):
    queue = message_queues.get(token_id)
    if queue:
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
            except:
                pass
        return jsonify({"success": True})
    return jsonify({"error": "No queue"}), 400

@app.route("/set_delay", methods=["POST"])
def set_delay():
    try:
        min_val = float(request.form.get("delay_min"))
        max_val = float(request.form.get("delay_max"))
        if min_val <= max_val and min_val >= 0.1:
            config["delay_min"] = min_val
            config["delay_max"] = max_val
            return jsonify({"status": "success"})
    except:
        pass
    return jsonify({"status": "error"}), 400

@app.route("/set_status", methods=["POST"])
def set_status():
    new_status = request.form.get("status")
    if new_status in STATUS_MAP:
        config["current_status"] = new_status
        for client in discord_clients.values():
            asyncio.run_coroutine_threadsafe(
                client.change_presence(status=STATUS_MAP[new_status]),
                client.loop
            )
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

if __name__ == "__main__":
    start_all_bots()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
