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

# --- 1. SOZLAMALAR VA KALITLAR ---
def get_env(key, default=""):
    val = os.environ.get(key, default)
    if val is None or not str(val).strip():
        return default
    return str(val).strip()

DEFAULT_BOT_TOKEN = "6722502116:AAH8nMf9Er0Al0yR_S5kmPlSMRFadRoT8uk"
TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", DEFAULT_BOT_TOKEN)
if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = DEFAULT_BOT_TOKEN

GEMINI_API_KEY = get_env("GEMINI_API_KEY")
GROQ_API_KEY = get_env("GROQ_API_KEY")
SPREADSHEET_ID = get_env("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = get_env("GOOGLE_CREDENTIALS_JSON")
CHANNEL_CHAT_ID = get_env("CHANNEL_CHAT_ID", "@obsidian_lab_uz")

user_histories = {}

latest_ict_status = {
    "BTC": "Scanning 15m FVG Liquidity...",
    "ETH": "Monitoring CISD delivery...",
    "last_signal": "No high-probability setup yet",
    "active_agents": 3
}

# AI Mijozlari
gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"⚠️ Gemini ogohlantirish: {e}")

groq_client = None
if GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        print(f"⚠️ Groq ogohlantirish: {e}")

# Google Sheets
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
        print(f"⚠️ Google Sheets ogohlantirish: {e}")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)
CORS(app)

# --- 2. AI TAHLIL ---
def get_ai_analysis(prompt: str) -> str:
    if gemini_client:
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.45,
                    max_output_tokens=900,
                ),
            )
            text = (response.text or "").strip()
            if text:
                return text
        except Exception as gemini_err:
            print(f"⚠️ Gemini xatolik: {gemini_err}")

    if groq_client:
        try:
            completion = groq_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=900,
            )
            text = (completion.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception as groq_err:
            print(f"⚠️ Groq xatolik: {groq_err}")

    return None

def get_trades_from_sheets():
    if not sheet:
        return []
    try:
        records = sheet.get_all_records()
        trades = []
        for r in records:
            trades.append({
                "id": str(r.get("ID", "")),
                "title": str(r.get("Sarlavha", "Nomsiz")),
                "content": str(r.get("Tahlil", "")),
                "date": str(r.get("Sana", ""))
            })
        return trades
    except Exception:
        return []

def save_trade_to_sheets(title, content):
    global sent_trade_ids
    if not sheet:
        return False, "Google Sheets ulanmagan"
    try:
        t_id = str(uuid.uuid4())[:8]
        uzb_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
        date_str = uzb_time.strftime("%Y-%m-%d")
        sheet.append_row([t_id, title[:100], content[:2000], date_str])
        sent_trade_ids.add(t_id)  # Qayta jo'natmasligi uchun keshga oladi
        return True, "Muvaffaqiyatli saqlandi"
    except Exception as e:
        return False, str(e)

# --- 3. FLASK API ---
@app.route('/', methods=['GET'])
def home():
    trades = get_trades_from_sheets()
    return render_template('index.html', trades=trades)

@app.route('/api/update_balance', methods=['POST'])
def update_balance():
    global spreadsheet
    try:
        data = request.get_json(force=True) or {}
        user_id = str(data.get("user_id", "")).strip()
        username = str(data.get("username", "Trader")).strip()
        balance = float(data.get("balance", 10000.0))

        if not spreadsheet or not user_id:
            return jsonify({"status": "error"}), 400

        ws = spreadsheet.worksheet("Leaderboard")
        all_vals = ws.get_all_values()
        row_to_update = None
        for i, row in enumerate(all_vals[1:], start=2):
            if len(row) >= 1 and row[0].strip() == user_id:
                row_to_update = i
                break

        formatted_bal = f"{balance:.2f}"
        if row_to_update:
            ws.update_cell(row_to_update, 2, username)
            ws.update_cell(row_to_update, 3, formatted_bal)
        else:
            ws.append_row([user_id, username, formatted_bal])

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    global spreadsheet
    try:
        if not spreadsheet:
            return jsonify({"status": "success", "leaders": []})
        ws = spreadsheet.worksheet("Leaderboard")
        records = ws.get_all_records()
        valid_leaders = []
        for r in records:
            raw_bal = str(r.get("Balance", "0")).replace(" ", "").replace(",", ".")
            try:
                bal_val = float(raw_bal)
            except Exception:
                bal_val = 0.0
            valid_leaders.append({
                "User ID": str(r.get("User ID", "")),
                "Username": str(r.get("Username", "Trader")),
                "Balance": bal_val
            })
        sorted_leaders = sorted(valid_leaders, key=lambda x: x["Balance"], reverse=True)[:10]
        return jsonify({"status": "success", "leaders": sorted_leaders})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/npc_chat', methods=['POST'])
def npc_chat():
    try:
        data = request.get_json(force=True) or {}
        npc_id = data.get("npc_id", "jasur")
        user_msg = data.get("message", "").strip()
        if not user_msg:
            return jsonify({"status": "error", "reply": "Biror narsa gapir, jigar."}), 400

        prompts = {
            "jasur": (
                "Sen — Jasur, tuni bilan grafik tahlil qilgan, charchagan Toshkentlik ICT treydersan. "
                "Qo'lingda kofe, ko'zlaring qizargan. FVG va Turtle Soup haqida gapirasan. "
                "Javobing 1-2 jumla, samimiy, charchoq va o'tkir kinoya aralash bo'lsin. "
                f"Foydalanuvchi aytdi: '{user_msg}'. Javob ber:"
            ),
            "alex": (
                "Sen — Alex, sovuqqon algo-treyder va kordersan. Faqat matematika, kod va ICT algoritmi bilan gapirasan. "
                f"Javobing 1-2 jumla, aniq va texnik bo'lsin. Savol: '{user_msg}'"
            ),
            "whale": (
                "Sen — Mister Whale, million dollarlik kapital egasisan. O'ta vazmin, mulohazali va boy odamsan. "
                f"Javobing 1-2 jumla, xotirjam va salobatli bo'lsin. Savol: '{user_msg}'"
            )
        }
        reply = get_ai_analysis(prompts.get(npc_id, prompts["jasur"])) or "Uyg'oqman, nima gap jigar?"
        return jsonify({"status": "success", "reply": reply})
    except Exception as e:
        return jsonify({"status": "error", "reply": f"Xatolik: {e}"}), 500

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- 4. SIGNAL SKANERI ---
def scan_and_post_ai_signals():
    symbols = ["BTCUSDT", "ETHUSDT"]
    while True:
        try:
            for sym in symbols:
                url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=15m&limit=25"
                res = requests.get(url, timeout=10)
                if res.status_code != 200:
                    continue
                candles = res.json()
                current_price = float(candles[-1][4])
                prompt = f"{sym} 15m ICT setup tekshir. Joriy narx: {current_price}. Agar Turtle Soup/FVG bo'lsa SIGNAL_FOUND bilan boshla, aks holda NO_SIGNAL deb yoz."
                ai_verdict = get_ai_analysis(prompt)
                if ai_verdict and "SIGNAL_FOUND" in ai_verdict:
                    clean_text = ai_verdict.replace("SIGNAL_FOUND", "").strip()
                    bot.send_message(CHANNEL_CHAT_ID, f"⚡️ <b>OBSIDIAN ICT SIGNAL</b>\n#{sym} - ${current_price}\n\n{clean_text}", parse_mode="HTML")
                    save_trade_to_sheets(f"ICT: {sym}", clean_text)
                time.sleep(5)
        except Exception as e:
            print(f"Radar xatolik: {e}")
        time.sleep(900)

# --- 5. GOOGLE SHEETS MONITORING (XATOLIK TO'G'RILANDI) ---
sent_trade_ids = set()

def monitor_new_trades():
    global sent_trade_ids
    # 1. Dastlabki mavjud barcha eski bitimlarni yig'ib olish (Qayta jo'natmaslik uchun)
    try:
        existing = get_trades_from_sheets()
        for t in existing:
            if t.get("id"):
                sent_trade_ids.add(str(t["id"]).strip())
        print(f"📦 Boshlang'ich {len(sent_trade_ids)} ta eski bitim eslab qolindi.")
    except Exception as e:
        print(f"Eski bitimlarni olishda ogohlantirish: {e}")

    # 2. Faqat haqiqiy yangi bitimlarni kuzatish sikli
    while True:
        try:
            time.sleep(45)
            trades = get_trades_from_sheets()
            for trade in trades:
                t_id = str(trade.get("id", "")).strip()
                if t_id and t_id not in sent_trade_ids:
                    title = trade.get("title", "Yangi Bitim")
                    content = trade.get("content", "")
                    signal_text = f"🚨 <b>YANGI SIGNAL / SAVDO!</b>\n\n📌 <b>{title}</b>\n\n{content}"
                    try:
                        bot.send_message(CHANNEL_CHAT_ID, signal_text, parse_mode="HTML")
                        sent_trade_ids.add(t_id)
                    except Exception as send_err:
                        print(f"Kanalga yuborishda xatolik: {send_err}")
        except Exception:
            pass

# --- 6. AVTOMATIK YANGILIKLAR TIZIMI (XATOLIK TO'G'RILANDI) ---
SEEN_NEWS = set()

def fetch_and_post_crypto_news():
    global SEEN_NEWS
    # Dastlabki yangiliklarni yozib olish (qayta-qayta yubormasligi uchun)
    try:
        feed = feedparser.parse("https://cointelegraph.com/rss")
        for entry in feed.entries[:5]:
            if entry.get("link"):
                SEEN_NEWS.add(entry.get("link"))
    except Exception:
        pass

    while True:
        try:
            feed_url = "https://cointelegraph.com/rss"
            feed = feedparser.parse(feed_url)
            if feed.entries:
                latest = feed.entries[0]
                title = latest.get("title", "")
                raw_summary = latest.get("summary", "")[:400]
                link = latest.get("link", "")

                if link and link not in SEEN_NEWS:
                    clean_summary = re.sub(r'<[^>]+>', '', raw_summary).strip()
                    prompt = f"""Sen kripto yangiliklarini o'zbek tilida qisqa (2 jumla) tushuntiruvchi AIsan.
Sarlavha: {title}
Mazmuni: {clean_summary}
Format:
⚡️ *MARKET ALERT*
📌 *{title}*
{clean_summary[:200]}...
"""
                    post_text = get_ai_analysis(prompt) or f"⚡️ *MARKET ALERT*\n📌 *{title}*\n{clean_summary[:200]}..."
                    try:
                        bot.send_message(CHANNEL_CHAT_ID, post_text, parse_mode="Markdown", disable_web_page_preview=True)
                        SEEN_NEWS.add(link)
                    except Exception:
                        pass

        except Exception as e:
            print(f"LOG: Yangiliklar xatolik: {e}")

        time.sleep(3600)

@bot.message_handler(commands=['start'])
def handle_start(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🚀 Savdo Terminalini ochish", web_app=WebAppInfo(url="https://akbarjon1.github.io/tradebot/")))
    bot.reply_to(message, "⚡️ *Obsidian Lab platformasiga xush kelibsiz!*", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: True)
def handle_msg(m):
    p = f"Sen samimiy Toshkentlik treydersan. Qisqa javob ber. Savol: {m.text}"
    bot.send_message(m.chat.id, get_ai_analysis(p) or "Eshitaman jigar!")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=monitor_new_trades, daemon=True).start()
    threading.Thread(target=fetch_and_post_crypto_news, daemon=True).start()
    threading.Thread(target=scan_and_post_ai_signals, daemon=True).start()
    bot.infinity_polling()
