import os
import sys
import asyncio
import threading
import random
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

# ========== VERIFICARE DISCORD ==========
try:
    import discord
    from discord.ext import tasks
    DISCORD_OK = True
except ImportError as e:
    print(f"❌ Eroare import discord: {e}")
    DISCORD_OK = False

# ========== VERIFICARE MONGO ==========
try:
    from pymongo import MongoClient
    from bson import ObjectId
    MONGO_URI = os.environ.get("MONGO_URI", "")
    if MONGO_URI:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        db = client["robycord"]
        tokens_col = db["tokens"]
        MONGO_OK = True
        print("✅ MongoDB conectat")
    else:
        MONGO_OK = False
        print("⚠️ MONGO_URI nu e setat")
except Exception as e:
    print(f"❌ Eroare MongoDB: {e}")
    MONGO_OK = False

# ========== STOCARE LOCALĂ DACĂ NU E MONGO ==========
if not MONGO_OK:
    tokens_col = None
    local_tokens = []
    
    def save_token_local(token_str):
        token_data = {
            "_id": str(len(local_tokens) + 1),
            "token": token_str,
            "username": "Unknown",
            "status": "offline",
            "added_at": datetime.now(),
            "last_seen": None
        }
        local_tokens.append(token_data)
        return token_data
    
    def get_all_tokens_local():
        return local_tokens
    
    def get_token_local(token_id):
        for t in local_tokens:
            if t["_id"] == token_id:
                return t
        return None
    
    def delete_token_local(token_id):
        global local_tokens
        local_tokens = [t for t in local_tokens if t["_id"] != token_id]
    
    def update_token_local(token_id, updates):
        for t in local_tokens:
            if t["_id"] == token_id:
                t.update(updates)
                break

# ========== DISCORD CLIENTS ==========
discord_clients = {}
message_queues = {}

config = {
    "delay_min": 5.0,
    "delay_max": 10.0,
    "current_status": "dnd",
}

STATUS_MAP = {
    "online": discord.Status.online if DISCORD_OK else None,
    "idle": discord.Status.idle if DISCORD_OK else None,
    "dnd": discord.Status.dnd if DISCORD_OK else None,
    "offline": discord.Status.invisible if DISCORD_OK else None
}

class MySelfBot(discord.Client):
    def __init__(self, token_id, token, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token_id = token_id
        self.token = token

    async def on_ready(self):
        print(f'[Self-Bot] {self.user} conectat')
        await self.change_presence(status=STATUS_MAP.get(config["current_status"], discord.Status.online))
        
        if MONGO_OK and tokens_col is not None:
            tokens_col.update_one(
                {"_id": self.token_id},
                {"$set": {"username": str(self.user), "status": "online", "last_seen": datetime.now()}}
            )
        else:
            update_token_local(self.token_id, {"username": str(self.user), "status": "online"})
        
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
            await channel.send(final_message)
            print(f"[{self.user}] Trimis")
        except Exception as e:
            print(f"[Eroare] {e}")

        sleep_time = random.uniform(config["delay_min"], config["delay_max"])
        await asyncio.sleep(sleep_time)
        queue.task_done()

# ========== FUNCȚII TOKEN ==========
def load_tokens():
    if MONGO_OK and tokens_col is not None:
        return list(tokens_col.find())
    return get_all_tokens_local()

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
        try:
            loop.run_until_complete(client.start(token))
        except Exception as e:
            print(f"❌ Eroare pornire bot {token_id}: {e}")
    
    thread = threading.Thread(target=run, daemon=True)
    thread.start()

def start_all_bots():
    if not DISCORD_OK:
        return
    for token_data in load_tokens():
        start_bot_for_token(token_data)

# ========== RUTE ==========
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "discord": DISCORD_OK,
        "mongo": MONGO_OK,
        "python": sys.version
    })

@app.route("/api/tokens", methods=["GET"])
def get_tokens():
    tokens = load_tokens()
    result = []
    for t in tokens:
        tid = str(t["_id"]) if MONGO_OK else t["_id"]
        result.append({
            "id": tid,
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
    
    if MONGO_OK and tokens_col is not None:
        existing = tokens_col.find_one({"token": token})
        if existing:
            return jsonify({"error": "Token exists"}), 400
        token_data = {"token": token, "username": "Unknown", "status": "offline", "added_at": datetime.now()}
        result = tokens_col.insert_one(token_data)
        return jsonify({"success": True, "id": str(result.inserted_id)})
    else:
        token_data = save_token_local(token)
        return jsonify({"success": True, "id": token_data["_id"]})

@app.route("/api/tokens/<token_id>/start", methods=["POST"])
def start_token(token_id):
    if MONGO_OK and tokens_col is not None:
        from bson import ObjectId
        token_data = tokens_col.find_one({"_id": ObjectId(token_id)})
    else:
        token_data = get_token_local(token_id)
    
    if not token_data:
        return jsonify({"error": "Not found"}), 404
    
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
        
        if MONGO_OK and tokens_col is not None:
            from bson import ObjectId
            tokens_col.update_one({"_id": ObjectId(token_id)}, {"$set": {"status": "offline"}})
        else:
            update_token_local(token_id, {"status": "offline"})
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
    
    if MONGO_OK and tokens_col is not None:
        from bson import ObjectId
        tokens_col.delete_one({"_id": ObjectId(token_id)})
    else:
        delete_token_local(token_id)
    return jsonify({"success": True})

@app.route("/api/tokens/<token_id>/send", methods=["POST"])
def send_message(token_id):
    data = request.json or {}
    channel_id = data.get("channel_id")
    message = data.get("message")
    user_mention = data.get("user_mention", "")
    
    if not channel_id or not message:
        return jsonify({"error": "Missing fields"}), 400
    
    queue = message_queues.get(token_id)
    if queue is None:
        return jsonify({"error": "Bot not running"}), 400
    
    asyncio.run_coroutine_threadsafe(
        queue.put({
            "channel_id": channel_id,
            "user_mention": user_mention,
            "message": message
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
    if new_status in STATUS_MAP and DISCORD_OK:
        config["current_status"] = new_status
        for client in discord_clients.values():
            asyncio.run_coroutine_threadsafe(
                client.change_presence(status=STATUS_MAP[new_status]),
                client.loop
            )
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

if __name__ == "__main__":
    print(f"🚀 Pornire RobyCord")
    print(f"📦 Discord: {'✅' if DISCORD_OK else '❌'}")
    print(f"🗄️ MongoDB: {'✅' if MONGO_OK else '⚠️ (local storage)'}")
    
    if DISCORD_OK:
        start_all_bots()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
