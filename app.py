import os
import asyncio
import threading
import random
import logging
from flask import Flask, render_template, request, jsonify
import discord
from discord.ext import tasks

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

DISCORD_TOKEN = "AICI_PUI_TOKENUL_TAU_DISCORD"

message_queue = None  
discord_client = None

config = {
    "delay_min": 5.0,
    "delay_max": 10.0,
    "current_status": "dnd",
    "simulate_typing": False
}

STATUS_MAP = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "offline": discord.Status.invisible
}

class MySelfBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def on_ready(self):
        print(f'[Self-Bot] Conectat ca {self.user}')
        await self.change_presence(status=STATUS_MAP.get(config["current_status"], discord.Status.online))
        self.process_queue.start()

    @tasks.loop(seconds=0.1)
    async def process_queue(self):
        global message_queue
        if message_queue is None or message_queue.empty():
            return

        item = await message_queue.get()
        
        channel_id = int(item["channel_id"])
        user_mention = item["user_mention"]
        message_text = item["message"]
        should_type = item["simulate_typing"]
        
        channel = self.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception as e:
                print(f"[Eroare] Canal {channel_id}: {e}")
                message_queue.task_done()
                return

        final_message = message_text
        if user_mention and user_mention.strip():
            clean_mention = user_mention.replace("<", "").replace("@", "").replace(">", "").strip()
            final_message = f"<@{clean_mention}> {final_message}"

        try:
            if should_type:
                async with channel.typing():
                    await asyncio.sleep(2.5)
            
            await channel.send(final_message)
            print(f"[Self-Bot] Trimis în #{channel.name}")

        except Exception as e:
            print(f"[Eroare Trimitere] {e}")

        sleep_time = random.uniform(config["delay_min"], config["delay_max"])
        await asyncio.sleep(sleep_time)
        message_queue.task_done()

@app.route("/")
def index():
    return render_template(
        "index.html", 
        delay_min=config["delay_min"], 
        delay_max=config["delay_max"],
        current_status=config["current_status"]
    )

@app.route("/send", methods=["POST"])
def send_message():
    global message_queue, discord_client
    channel_id = request.form.get("channel_id")
    user_mention = request.form.get("user_mention")
    message = request.form.get("message")
    simulate_typing = request.form.get("simulate_typing") == "true"
    
    if not channel_id or not message:
        return jsonify({"status": "error"}), 400
        
    if message_queue is None or discord_client is None:
        return jsonify({"status": "error"}), 500

    asyncio.run_coroutine_threadsafe(
        message_queue.put({
            "channel_id": channel_id,
            "user_mention": user_mention,
            "message": message,
            "simulate_typing": simulate_typing
        }),
        discord_client.loop
    )
    return jsonify({"status": "success", "queue_count": message_queue.qsize()})

@app.route("/set_delay", methods=["POST"])
def set_delay():
    try:
        min_val = float(request.form.get("delay_min"))
        max_val = float(request.form.get("delay_max"))
        if min_val <= max_val and min_val >= 0.1:
            config["delay_min"] = min_val
            config["delay_max"] = max_val
            return jsonify({"status": "success", "delay_min": config["delay_min"], "delay_max": config["delay_max"]})
    except (ValueError, TypeError):
        pass
    return jsonify({"status": "error"}), 400

@app.route("/set_status", methods=["POST"])
def set_status():
    global discord_client
    new_status = request.form.get("status")
    if new_status in STATUS_MAP and discord_client:
        config["current_status"] = new_status
        asyncio.run_coroutine_threadsafe(
            discord_client.change_presence(status=STATUS_MAP[new_status]),
            discord_client.loop
        )
        return jsonify({"status": "success", "current_status": config["current_status"]})
    return jsonify({"status": "error"}), 400

@app.route("/get_queue", methods=["GET"])
def get_queue():
    q_size = message_queue.qsize() if message_queue else 0
    return jsonify({
        "queue_count": q_size,
        "current_delay_min": config["delay_min"],
        "current_delay_max": config["delay_max"],
        "current_status": config["current_status"]
    })

@app.route("/clear_queue", methods=["POST"])
def clear_queue():
    global message_queue
    if message_queue:
        while not message_queue.empty():
            try:
                message_queue.get_nowait()
                message_queue.task_done()
            except:
                pass
        return jsonify({"status": "success", "message": "Queue cleared"})
    return jsonify({"status": "error", "message": "No queue"}), 400

def run_discord():
    global message_queue, discord_client
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    message_queue = asyncio.Queue()
    discord_client = MySelfBot()
    
    loop.run_until_complete(discord_client.start(DISCORD_TOKEN))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    threading.Thread(target=run_discord, daemon=True).start()
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
