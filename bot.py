import os
import re
import time
import json
import uuid
import datetime
import threading
import requests
import feedparser
import telebot
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, render_template, request, redirect, url_for, jsonify
from google import genai
from google.genai import types as genai_types
from groq import Groq
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from flask_cors import CORS

def get_env(key, default=""):
    val = os.environ.get(key, default)
    return val.strip() if val else default

TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", "6722502116:AAH8nMf9Er0Al0yR_S5kmPlSMRFadRoT8uk")
GEMINI_API_KEY = get_env("GEMINI_API_KEY")
GROQ_API_KEY = get_env("GROQ_API_KEY")
SPREADSHEET_ID = get_env("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = get_env("GOOGLE_CREDENTIALS_JSON")
CHANNEL_CHAT_ID = get_env("CHANNEL_CHAT_ID", "@obsidian_lab_uz")

user_histories = {}
latest_ict_status = {"BTC": "Scanning 15m FVG Liquidity...", "ETH": "Monitoring CISD delivery..."}

# ONLINE O'YINCHILAR VA CHATLAR KESHI (MULTIPLAYER UCHUN)
online_players = {}  # {user_id: {x, y, room, username, last_seen}}
global_chat_messages = [] # [{sender, text, room, time}]

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

sheet = None
spreadsheet = None

if GOOGLE_CREDENTIALS_JSON and SPREADSHEET_ID:
    try:
        cred_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(cred_info, scopes=scopes)
        gc = gspread.authorize(credentials)
        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.sheet1
        print("✅ Google Sheets ulandi!")
    except Exception as e:
        print(f"⚠️ Google Sheets xatolik: {e}")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)
CORS(app)

def get_ai_analysis(prompt: str) -> str:
    if gemini_client:
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt,
                config=genai_types.GenerateContentConfig(temperature=0.45, max_output_tokens=900),
            )
            text = (response.text or "").strip()
            if text: return text
        except Exception: pass

    if groq_client:
        try:
            completion = groq_client.chat.completions.create(
                model="openai/gpt-oss-120b", messages=[{"role": "user", "content": prompt}],
                temperature=0.5, max_tokens=900,
            )
            text = (completion.choices[0].message.content or "").strip()
            if text: return text
        except Exception: pass
    return None

def get_trades_from_sheets():
    if not sheet: return []
    try:
        records = sheet.get_all_records()
        return [{"id": str(r.get("ID", "")), "title": str(r.get("Sarlavha", "Nomsiz")), "content": str(r.get("Tahlil", "")), "date": str(r.get("Sana", ""))} for r in records]
    except Exception: return []

def save_trade_to_sheets(title, content):
    global sent_trade_ids
    if not sheet: return False, "Google Sheets ulanmagan"
    try:
        t_id = str(uuid.uuid4())[:8]
        uzb_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
        sheet.append_row([t_id, title[:100], content[:2000], uzb_time.strftime("%Y-%m-%d")])
        return True, "Muvaffaqiyatli saqlandi"
    except Exception as e:
        return False, str(e)

@app.route('/', methods=['GET'])
def home():
    return render_template('index.html', trades=get_trades_from_sheets())

# --- MULTIPLAYER API: O'YINCHILARNING POZITSIYASI VA CHATI ---
@app.route('/api/multiplayer/sync', methods=['POST'])
def multiplayer_sync():
    try:
        data = request.get_json(force=True) or {}
        uid = str(data.get("user_id", ""))
        if not uid: return jsonify({"status": "error"}), 400

        online_players[uid] = {
            "user_id": uid,
            "username": str(data.get("username", "@trader")),
            "x": float(data.get("x", 300)),
            "y": float(data.get("y", 340)),
            "room": str(data.get("room", "office")),
            "last_seen": time.time()
        }

        # Eskirgan o'yinchilarni tozalash (10 soniyadan beri ping bermaganlar)
        now = time.time()
        inactive = [k for k, v in online_players.items() if now - v["last_seen"] > 10]
        for k in inactive: del online_players[k]

        # Shu xonadagi boshqa o'yinchilarni qaytarish
        current_room = data.get("room", "office")
        active_others = [p for p in online_players.values() if p["user_id"] != uid and p["room"] == current_room]

        return jsonify({"status": "success", "players": active_others})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/multiplayer/chat', methods=['POST'])
def multiplayer_chat():
    try:
        data = request.get_json(force=True) or {}
        sender = str(data.get("sender", "@trader"))
        text = str(data.get("text", "")).strip()
        room = str(data.get("room", "office"))
        if text:
            uzb_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
            global_chat_messages.append({
                "sender": sender, "text": text, "room": room,
                "time": uzb_time.strftime("%H:%M")
            })
            if len(global_chat_messages) > 50: global_chat_messages.pop(0)
        room_msgs = [m for m in global_chat_messages if m["room"] == room][-15:]
        return jsonify({"status": "success", "messages": room_msgs})
    except Exception as e:
        return jsonify({"status": "error", "messages": []}), 500

@app.route('/api/update_balance', methods=['POST'])
def update_balance():
    global spreadsheet
    try:
        data = request.get_json(force=True) or {}
        user_id = str(data.get("user_id", "")).strip()
        username = str(data.get("username", "Trader")).strip()
        balance = float(data.get("balance", 10000.0))
        if not spreadsheet or not user_id: return jsonify({"status": "error"}), 400
        ws = spreadsheet.worksheet("Leaderboard")
        all_vals = ws.get_all_values()
        row_to_update = None
        for i, row in enumerate(all_vals[1:], start=2):
            if len(row) >= 1 and row[0].strip() == user_id: row_to_update = i; break
        formatted_bal = f"{balance:.2f}"
        if row_to_update:
            ws.update_cell(row_to_update, 2, username)
            ws.update_cell(row_to_update, 3, formatted_bal)
        else:
            ws.append_row([user_id, username, formatted_bal])
        return jsonify({"status": "success"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    global spreadsheet
    try:
        if not spreadsheet: return jsonify({"status": "success", "leaders": []})
        ws = spreadsheet.worksheet("Leaderboard")
        records = ws.get_all_records()
        valid_leaders = [{"User ID": str(r.get("User ID", "")), "Username": str(r.get("Username", "Trader")), "Balance": float(str(r.get("Balance", "0")).replace(" ", "").replace(",", "."))} for r in records]
        return jsonify({"status": "success", "leaders": sorted(valid_leaders, key=lambda x: x["Balance"], reverse=True)[:10]})
    except Exception as e: return jsonify({"status": "error", "leaders": []}), 500

@app.route('/api/npc_chat', methods=['POST'])
def npc_chat():
    try:
        data = request.get_json(force=True) or {}
        npc_id = data.get("npc_id", "jasur")
        user_msg = data.get("message", "").strip()
        prompts = {
            "jasur": f"Sen — Jasur, charchagan ICT treydersan. Toshkentcha qisqa javob ber: {user_msg}",
            "alex": f"Sen — Alex, sovuqqon algo-treydersan. Qisqa texnik javob ber: {user_msg}",
            "whale": f"Sen — Mister Whale, millioner kit. Xotirjam maslahat ber: {user_msg}"
        }
        reply = get_ai_analysis(prompts.get(npc_id, prompts["jasur"])) or "Eshitaman jigar!"
        return jsonify({"status": "success", "reply": reply})
    except Exception as e: return jsonify({"status": "error", "reply": f"Xatolik: {e}"}), 500

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

sent_trade_ids = set()
def monitor_new_trades():
    global sent_trade_ids
    try:
        for t in get_trades_from_sheets():
            if t.get("id"): sent_trade_ids.add(str(t["id"]).strip())
    except Exception: pass
    while True:
        try:
            time.sleep(60)
            for trade in get_trades_from_sheets():
                t_id = str(trade.get("id", "")).strip()
                if t_id and t_id not in sent_trade_ids:
                    bot.send_message(CHANNEL_CHAT_ID, f"🚨 <b>YANGI SIGNAL!</b>\n\n📌 <b>{trade.get('title')}</b>\n\n{trade.get('content')}", parse_mode="HTML")
                    sent_trade_ids.add(t_id)
        except Exception: pass

@bot.message_handler(commands=['start'])
def handle_start(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🚀 Savdo Terminalini ochish", web_app=WebAppInfo(url="https://akbarjon1.github.io/tradebot/")))
    bot.reply_to(message, "⚡️ *Obsidian Lab platformasiga xush kelibsiz!*", reply_markup=markup, parse_mode="Markdown")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=monitor_new_trades, daemon=True).start()
    bot.infinity_polling()
