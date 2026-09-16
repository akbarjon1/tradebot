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

# Xatoliksiz to'g'ridan-to'g'ri ishga tushuvchi token
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
        print(f"⚠️ Gemini ulanishda ogohlantirish: {e}")

groq_client = None
if GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        print(f"⚠️ Groq ulanishda ogohlantirish: {e}")

# Google Sheets mijozi
sheet = None
spreadsheet = None

def format_google_sheet(sh, sp):
    try:
        sheet_id = sh.id
        requests_list = [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1}
                    },
                    "fields": "gridProperties.frozenRowCount"
                }
            }
        ]
        sp.batch_update({"requests": requests_list})
    except Exception:
        pass

if GOOGLE_CREDENTIALS_JSON and SPREADSHEET_ID:
    try:
        cred_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(cred_info, scopes=scopes)
        gc = gspread.authorize(credentials)
        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.sheet1
        print("✅ Google Sheets ulandi!")
        format_google_sheet(sheet, spreadsheet)
    except Exception as e:
        print(f"⚠️ Google Sheets ogohlantirish: {e}")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)
CORS(app)

# --- 2. UNIVERSAL AI TAHLIL FUNKSIYASI ---
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

# --- 3. GOOGLE SHEETS FUNKSIYALARI ---
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
    if not sheet:
        return False, "Google Sheets ulanmagan"
    try:
        t_id = str(uuid.uuid4())[:8]
        uzb_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
        date_str = uzb_time.strftime("%Y-%m-%d")
        sheet.append_row([t_id, title[:100], content[:2000], date_str])
        return True, "Muvaffaqiyatli saqlandi"
    except Exception as e:
        return False, str(e)

# --- 4. FLASK WEB SAYTI VA API ENDPOINTS ---
CHANNEL_ID = "-5436696482"
comments_store = []

@app.route('/', methods=['GET'])
def home():
    trades = get_trades_from_sheets()
    return render_template(
        'index.html',
        title="Obsidian Lab — Trade Smarter. Real Markets. Zero Risk.",
        username="@trader",
        api_base="https://tradebot-xelo.onrender.com",
        bot_url="https://t.me/your_bot_username",
        trades=trades,
        comments=comments_store
    )

@app.route('/add_comment', methods=['POST'])
def add_comment():
    user_comment = request.form.get('comment')
    if user_comment:
        uzb_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
        now_time = uzb_time.strftime("%Y-%m-%d %H:%M")

        comments_store.insert(0, {
            'text': user_comment,
            'created_at': now_time
        })
        try:
            tg_text = (
                f"💬 *OBSIDIAN LAB // FEEDBACK*\n"
                f"⏱ *Vaqt:* `{now_time}`\n"
                f"👤 *Manba:* `Web Terminal`\n"
                f"───────────────────\n\n"
                f"📝 *Fikr / Izoh:*\n"
                f"« {user_comment} »\n\n"
                f"▫️ _Status: Qabul qilindi_"
            )
            bot.send_message(CHANNEL_ID, tg_text, parse_mode="Markdown")
        except Exception as e:
            print(f"Kanalga yuborishda xatolik: {e}")

    return redirect(url_for('home'))

@app.route('/api/update_balance', methods=['POST'])
def update_balance():
    global spreadsheet
    try:
        data = request.get_json(force=True) or {}
        user_id = str(data.get("user_id", "")).strip()
        username = str(data.get("username", "Trader")).strip()
        balance = float(data.get("balance", 10000.0))

        if not spreadsheet or not user_id:
            return jsonify({"status": "error", "message": "Noto'g'ri ma'lumot"}), 400

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

        return jsonify({"status": "success", "updated_row": row_to_update})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    global spreadsheet
    try:
        if not spreadsheet:
            return jsonify({"status": "success", "leaders": []})

        try:
            ws = spreadsheet.worksheet("Leaderboard")
        except Exception:
            return jsonify({"status": "success", "leaders": []})

        records = ws.get_all_records()
        valid_leaders = []

        for r in records:
            raw_bal = str(r.get("Balance", "0")).replace(" ", "").replace("\xa0", "").replace(",", ".")
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

# --- OBSIDIAN HQ: LIVE AGENT LOGS VA TASKS ENDPOINT ---
@app.route('/api/agent_tasks', methods=['GET'])
def get_agent_tasks():
    return jsonify({
        "status": "success",
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
        "agents": {
            "jasur": {
                "name": "Jasur",
                "role": "ICT Market Analyst",
                "current_task": latest_ict_status.get("BTC", "Scanning 15m Liquidity"),
                "location": "Central Desk"
            },
            "alex": {
                "name": "Alex",
                "role": "Algo & Quant Dev",
                "current_task": "Backtesting CISD v2.4",
                "location": "Server Terminal"
            },
            "whale": {
                "name": "Mister Whale",
                "role": "Capital & Risk Manager",
                "current_task": "Reviewing Portfolio PnL",
                "location": "VIP Lounge"
            }
        },
        "logs": [
            f"⚡️ Jasur: {latest_ict_status.get('BTC')}",
            "💻 Alex: PineScript & Python ICT Engine online",
            "🐋 Mister Whale: Risk threshold set to 1.5% max drawdown",
            "📡 Network: Binance 15m WebSocket latency: 28ms"
        ]
    })

# --- OBSIDIAN LOUNGE: AI AGENTLAR BILAN SUHBAT ---
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
                "Sen — Jasur, kechasi bilan grafik qarab chiqqan, charchagan, lekin tajribali ICT treydersan. "
                "Qo'lingda qahva, ko'zlaring qizargan. FVG, Turtle Soup, London/NY session likvidligi bo'yicha gapirasan. "
                "Gaplaring qisqa (1-2 jumla), samimiy, Toshkent ko'cha shevasida, charchoq va o'tkir kinoya aralash bo'lsin. "
                f"Treyder senga aytdi: '{user_msg}'. Unga javob ber:"
            ),
            "alex": (
                "Sen — Alex, sovuqqon algo-treyder va kordersan. Hissiyot nol, faqat matematika, kod va ICT algoritmi. "
                "Change in State of Delivery (CISD), algoritmik muvozanat va backtest haqida gapirasan. "
                "Qisqa (1-2 jumla), texnik va o'ta aniq javob ber. "
                f"Treyder senga aytdi: '{user_msg}'. Unga javob ber:"
            ),
            "whale": (
                "Sen — Mister Whale, ko'p millionli kapital boshqaruvchisi, katta kit. O'ta vazmin, mulohazali va boy odamsan. "
                "1-2 daqiqalik shovqinlarga parvo qilmaysan. Sabr, psixologiya va katta hovuzlarni tushuntirasan. "
                "Qisqa (1-2 jumla), xotirjam va salobatli javob ber. "
                f"Treyder senga aytdi: '{user_msg}'. Unga javob ber:"
            )
        }

        chosen_prompt = prompts.get(npc_id, prompts["jasur"])
        ai_reply = get_ai_analysis(chosen_prompt)

        if not ai_reply:
            ai_reply = "Hozircha server band, birozdan keyin kel..."

        return jsonify({"status": "success", "reply": ai_reply})
    except Exception as e:
        return jsonify({"status": "error", "reply": f"Xatolik: {e}"}), 500

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- 5. ICT (SMART MONEY CONCEPTS) AVTO-SIGNAL SKANERI ---
def scan_and_post_ai_signals():
    global latest_ict_status
    symbols = ["BTCUSDT", "ETHUSDT"]
    print("🚀 [AI Radar // ICT Edition] Smart Money skaneri ishga tushdi!")
    
    while True:
        try:
            for sym in symbols:
                url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=15m&limit=25"
                res = requests.get(url, timeout=10)
                if res.status_code != 200:
                    continue

                candles_raw = res.json()
                current_price = float(candles_raw[-1][4])

                candles_ohlc = []
                for c in candles_raw[-15:]:
                    t_str = datetime.datetime.fromtimestamp(c[0]/1000).strftime("%H:%M")
                    candles_ohlc.append({
                        "t": t_str,
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4])
                    })

                prompt = f"""
Sen ICT (Inner Circle Trader) va Smart Money Concepts bo'yicha professional institutsional treydersan.
Quyida {sym} aktivining 15 daqiqalik so'nggi shamchalari berilgan (Open, High, Low, Close):
{json.dumps(candles_ohlc)}
Joriy jonli narx: {current_price}

Quyidagi ICT konseptlarini qat'iy tekshir:
1. Liquidity Sweep / Turtle Soup
2. CISD (Change In State of Delivery)
3. FVG (Fair Value Gap)
4. Target (BSL yoki SSL)

Agar to'laqonli ICT kirish nuqtasi bo'lsa:
SIGNAL_FOUND
📍 Yo'nalish: [LONG yoki SHORT]
🎯 Take-Profit (BSL/SSL): [aniq narx]
🛑 Stop-Loss (Invalidation): [aniq narx]
💡 ICT Tahlil: [1-2 jumla o'zbek tilida]

Aks holda FAQAT "NO_SIGNAL" deb yoz.
"""
                ai_verdict = get_ai_analysis(prompt)

                if ai_verdict and "SIGNAL_FOUND" in ai_verdict:
                    clean_text = ai_verdict.replace("SIGNAL_FOUND", "").strip()
                    uzb_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
                    now_time = uzb_time.strftime("%H:%M")

                    latest_ict_status["BTC" if "BTC" in sym else "ETH"] = f"ICT Setup detected on {sym}!"

                    post_caption = (
                        f"⚡️ <b>OBSIDIAN RADAR // ICT ALGO SIGNAL</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>Aktiv:</b> #{sym}\n"
                        f"💵 <b>Narx:</b> ${current_price:,.2f}\n"
                        f"⏱ <b>Vaqt:</b> {now_time}\n\n"
                        f"{clean_text}\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 <a href='https://tradebot-xelo.onrender.com'>Web Terminalda ochish</a>"
                    )

                    try:
                        bot.send_message(CHANNEL_CHAT_ID, post_caption, parse_mode="HTML")
                    except Exception as err:
                        print(f"Kanalga yuborishda xatolik: {err}")

                    save_trade_to_sheets(f"ICT: {sym}", clean_text)
                else:
                    latest_ict_status["BTC" if "BTC" in sym else "ETH"] = f"Scanning {sym} 15m Liquidity..."

                time.sleep(5)

        except Exception as err:
            print(f"LOG: [AI Radar] Ogohlantirish: {err}")

        time.sleep(900)

# --- 6. TELEGRAM BOT HANDLERLAR ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    tma_button = KeyboardButton(
        text="🚀 Savdo Terminalini ochish", 
        web_app=WebAppInfo(url="https://akbarjon1.github.io/tradebot/")
    )
    markup.add(tma_button)

    bot.reply_to(
        message, 
        "⚡️ *Obsidian Lab Paper-Trading platformasiga xush kelibsiz!*\n\n"
        "Virtual $10,000 balans bilan savdo qilish uchun quyidagi tugmani bosing:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['test_signal'])
def handle_test_signal(message):
    uzb_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5)
    now_time = uzb_time.strftime("%H:%M")
    
    test_caption = (
        f"⚡️ <b>OBSIDIAN RADAR // ICT ALGO SIGNAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Aktiv:</b> #BTCUSDT\n"
        f"💵 <b>Narx:</b> $76,300.00\n"
        f"⏱ <b>Vaqt:</b> {now_time}\n\n"
        f"📍 <b>Yo'nalish:</b> LONG 🟢\n"
        f"🎯 <b>Take-Profit (BSL):</b> $78,500.00\n"
        f"🛑 <b>Stop-Loss (Invalidation):</b> $75,100.00\n"
        f"💡 <b>ICT Tahlil:</b> 15m Sell-side likvidligi (Turtle Soup) olindi va bullish CISD yuz berdi.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <a href='https://tradebot-xelo.onrender.com'>Web Terminalda ochish</a>"
    )
    try:
        bot.send_message(CHANNEL_CHAT_ID, test_caption, parse_mode="HTML")
        bot.reply_to(message, "✅ Test signali kanalga yuborildi!")
    except Exception as e:
        bot.reply_to(message, f"❌ Xatolik: {e}")

@bot.message_handler(func=lambda message: True)
def handle_trade_message(message):
    user_text = message.text
    user_id = message.from_user.id

    if user_id not in user_histories:
        user_histories[user_id] = []

    history_text = "\n".join(user_histories[user_id][-6:])

    prompt = (
        "Sen — Toshkentlik kripto-treyder do'stsan. Telegramda yaqin do'sting bilan gaplashyapsan.\n"
        "- Qisqa, samimiy va erkin gapir.\n"
        f"Oldingi gaplar:\n{history_text}\n\n"
        f"Do'sting: {user_text}\nSen:"
    )

    try:
        content = get_ai_analysis(prompt) or "Uyg'oqman, nima gap jigar?"
        user_histories[user_id].append(f"Foydalanuvchi: {user_text}")
        user_histories[user_id].append(f"Sen: {content}")
        bot.send_message(message.chat.id, content)
    except Exception as e:
        bot.send_message(message.chat.id, f"Xatolik: {e}")

# --- 7. GOOGLE SHEETS MONITORING ---
sent_trade_ids = set()

def monitor_new_trades():
    global sent_trade_ids
    while True:
        try:
            time.sleep(60)
            trades = get_trades_from_sheets()
            for trade in trades:
                t_id = trade.get("id")
                if t_id and t_id not in sent_trade_ids:
                    title = trade.get("title", "Yangi Bitim")
                    content = trade.get("content", "")
                    signal_text = f"🚨 <b>YANGI SIGNAL / SAVDO!</b>\n\n📌 <b>{title}</b>\n\n{content}"
                    try:
                        bot.send_message(CHANNEL_CHAT_ID, signal_text, parse_mode="HTML")
                        sent_trade_ids.add(t_id)
                    except Exception:
                        pass
        except Exception:
            pass

# --- 8. AVTOMATIK YANGILIKLAR TIZIMI ---
SEEN_NEWS = set()

def fetch_and_post_crypto_news():
    while True:
        try:
            feed_url = "https://cointelegraph.com/rss"
            feed = feedparser.parse(feed_url)
            if feed.entries:
                latest = feed.entries[0]
                title = latest.get("title", "")
                raw_summary = latest.get("summary", "")[:400]
                link = latest.get("link", "")

                if link not in SEEN_NEWS:
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

# --- 9. ISHGA TUSHIRISH ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=monitor_new_trades, daemon=True).start()
    threading.Thread(target=fetch_and_post_crypto_news, daemon=True).start()
    threading.Thread(target=scan_and_post_ai_signals, daemon=True).start()

    bot.infinity_polling()
