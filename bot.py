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
    return val.strip() if val else default

TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required")
GEMINI_API_KEY = get_env("GEMINI_API_KEY")
GROQ_API_KEY = get_env("GROQ_API_KEY")
SPREADSHEET_ID = get_env("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = get_env("GOOGLE_CREDENTIALS_JSON")
CHANNEL_CHAT_ID = get_env("CHANNEL_CHAT_ID", "@obsidian_lab_uz")

user_histories = {}

# Jonli agent vazifalari keshi (HQ Ticker uchun)
latest_ict_status = {
    "BTC": "Scanning 15m FVG Liquidity...",
    "ETH": "Monitoring CISD delivery...",
    "last_signal": "No high-probability setup yet",
    "active_agents": 3
}

# AI Mijozlari
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Google Sheets mijozi va avtomatik dizayn
sheet = None
spreadsheet = None

def format_google_sheet(sh, sp):
    """Google Jadvalni avtomatik ravishda chiroyli professional terminal qilib bezash"""
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
            },
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                    "properties": {"pixelSize": 90},
                    "fields": "pixelSize"
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
                    "properties": {"pixelSize": 240},
                    "fields": "pixelSize"
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
                    "properties": {"pixelSize": 480},
                    "fields": "pixelSize"
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 4},
                    "properties": {"pixelSize": 110},
                    "fields": "pixelSize"
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.11, "green": 0.12, "blue": 0.16},
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "textFormat": {
                                "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                                "fontSize": 11,
                                "bold": True
                            }
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4},
                    "cell": {
                        "userEnteredFormat": {
                            "wrapStrategy": "WRAP",
                            "verticalAlignment": "MIDDLE"
                        }
                    },
                    "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"
                }
            }
        ]
        sp.batch_update({"requests": requests_list})
        print("🎨 Google Sheets dizayni avtomatik ravishda bezatildi!")
    except Exception as e:
        print(f"Dizayn qo'llashda ogohlantirish: {e}")

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
        print(f"⚠️ Google Sheets ulanishda xatolik: {e}")

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
            print(f"⚠️ Gemini ishlamadi: {gemini_err}")

    if groq_client:
        try:
            print("⚡️ Zaxira: Groq ishga tushdi...")
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
    except Exception as e:
        print(f"Google Sheets o'qishda xatolik: {e}")
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
        err_msg = str(e)
        print(f"Google Sheets'ga yozishda xatolik: {err_msg}")
        return False, err_msg

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
        bot_url=get_env("BOT_USERNAME", "your_bot_username"),
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
        print(f"Xatolik update_balance: {e}")
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
        print(f"Leaderboard olishda xatolik: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- OBSIDIAN HQ: LIVE AGENT LOGS VA TASKS ENDPOINT ---
@app.route('/api/agent_tasks', methods=['GET'])
def get_agent_tasks():
    """OBSIDIAN HQ uchun jonli agent statuslari."""
    agents = {
        "jasur": {
            "name": "Jasur", "role": "ICT Market Analyst",
            "current_task": latest_ict_status.get("BTC", "Scanning BTC 15m liquidity"),
            "location": "Research Desk"
        },
        "alex": {
            "name": "Alex", "role": "Algo & Quant Dev",
            "current_task": "Backtesting CISD engine v2.4",
            "location": "Quant Lab"
        },
        "whale": {
            "name": "Mister Whale", "role": "Risk Manager",
            "current_task": "Reviewing portfolio exposure",
            "location": "Command Room"
        },
        "nova": {
            "name": "Nova", "role": "Market Research",
            "current_task": "Scanning macro & crypto headlines",
            "location": "News Desk"
        },
        "atlas": {
            "name": "Atlas", "role": "Ops & Infrastructure",
            "current_task": "Checking API / WebSocket health",
            "location": "Server Room"
        }
    }
    return jsonify({
        "status": "success",
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
        "agents": agents,
        "logs": [
            f"⚡️ Jasur: {latest_ict_status.get('BTC', 'Scanning BTC 15m liquidity')}",
            "💻 Alex: PineScript & Python ICT engine online",
            "🐋 Mister Whale: Risk threshold set to 1.5% max drawdown",
            "🧠 Nova: Macro / crypto news scan active",
            "🛰️ Atlas: Binance API + WebSocket health check running"
        ]
    })


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
                "Sen — Jasur, tajribali ICT treyder. FVG, Turtle Soup, liquidity sweep, CISD va "
                "London/NY session haqida gapirasan. Javob 1-2 jumla, samimiy Toshkentcha uslubda. "
                f"Treyder senga aytdi: '{user_msg}'. Unga javob ber:"
            ),
            "alex": (
                "Sen — Alex, sovuqqon algo-treyder va developer. CISD, backtest, risk/reward va "
                "algoritmik mantiqni aniq tushuntirasan. Javob 1-2 jumla, texnik va qisqa. "
                f"Treyder senga aytdi: '{user_msg}'. Unga javob ber:"
            ),
            "whale": (
                "Sen — Mister Whale, vazmin kapital va risk menejerisan. Pozitsiya hajmi, drawdown, "
                "risk/reward va psixologiyaga urg'u berasan. Javob 1-2 jumla, xotirjam. "
                f"Treyder senga aytdi: '{user_msg}'. Unga javob ber:"
            ),
            "nova": (
                "Sen — Nova, market research agent. Makro yangiliklar, sentiment, katalizatorlar va "
                "bozor kontekstini qisqa ajratasan. Tasdiqlanmagan faktni to'qima. 1-2 jumla. "
                f"Treyder senga aytdi: '{user_msg}'. Unga javob ber:"
            ),
            "atlas": (
                "Sen — Atlas, DevOps va infrastructure agent. API, WebSocket, latency, uptime va "
                "xatoliklarni diagnostika qilasan. Javob 1-2 jumla, konkret va texnik. "
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
    """Har 15 daqiqada shamchalarni ICT / Smart Money qoidalarida tekshiruvchi modul"""
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
1. Liquidity Sweep / Turtle Soup: Narx oldingi asosiy High (Buy-side) yoki Low (Sell-side) likvidligini olib, orqasiga qaytdimi?
2. CISD (Change In State of Delivery / Market Structure Shift): Yetkazib berish holati o'zgardimi, impulsiv qarama-qarshi shamcha paydo bo'ldimi?
3. FVG (Fair Value Gap): 3 ta ketma-ket shamcha oralig'ida Imbalance (muvozanatsizlik) bormi va narx unga mitigatsiya qildimi?
4. Target (BSL yoki SSL): Qarama-qarshi tomondagi likvidlik hovuzi qayerda?

Agar bozorda to'laqonli ICT kirish nuqtasi (Turtle Soup + CISD + FVG) shakllangan bo'lsa, xabarni aynan "SIGNAL_FOUND" bilan boshla:
SIGNAL_FOUND
📍 Yo'nalish: [LONG yoki SHORT]
🎯 Take-Profit (BSL/SSL): [aniq narx]
🛑 Stop-Loss (Invalidation): [aniq narx]
💡 ICT Tahlil: [Qaysi likvidlik olingani, FVG va CISD qanday yuz berganini 1-2 jumlada o'zbek tilida professional ifoda et]

Agar bozor flat bo'lsa, likvidlik olinmagan bo'lsa yoki shartlar to'liq bo'lmasa, FAQAT "NO_SIGNAL" deb yoz. Boshqa so'z qo'shma.
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
                        f"⚠️ <i>Kotletit qilmang, risk-menejment qoidalariga rioya qiling!</i>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 <a href='https://tradebot-xelo.onrender.com'>Web Terminalda ochish</a>"
                    )

                    chart_url = "https://charts.bitbo.io/chart-images/btc-usd-1d.png"
                    img_sent = False

                    try:
                        resp = requests.get(chart_url, timeout=8)
                        if resp.status_code == 200:
                            bot.send_photo(CHANNEL_CHAT_ID, resp.content, caption=post_caption, parse_mode="HTML")
                            img_sent = True
                    except Exception as img_err:
                        print(f"Rasm yuklashda ogohlantirish: {img_err}")

                    if not img_sent:
                        bot.send_message(CHANNEL_CHAT_ID, post_caption, parse_mode="HTML")

                    save_trade_to_sheets(f"ICT: {sym}", clean_text)
                    print(f"LOG: [AI Radar // ICT] {sym} bo'yicha Smart Money signali kanalga chiqdi!")
                else:
                    latest_ict_status["BTC" if "BTC" in sym else "ETH"] = f"Scanning {sym} 15m Liquidity..."

                time.sleep(5)

        except Exception as err:
            print(f"LOG: [AI Radar // ICT] Skanerda ogohlantirish: {err}")

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
        "Virtual $10,000 balans bilan savdo qilish uchun quyidagi tugmani bosing:\n\n"
        "🛠 _Adminlar uchun test buyrug'i:_ `/test_signal`",
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
        f"💡 <b>ICT Tahlil:</b> 15m Sell-side likvidligi (Turtle Soup) yechildi va bullish CISD yuz berdi. Narx $75,800 FVG zonasiga mitigatsiya qilib yuqoriga qaytmoqda.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <a href='https://tradebot-xelo.onrender.com'>Web Terminalda ochish</a>"
    )
    chart_url = "https://charts.bitbo.io/chart-images/btc-usd-1d.png"

    try:
        resp = requests.get(chart_url, timeout=8)
        if resp.status_code == 200:
            bot.send_photo(CHANNEL_CHAT_ID, resp.content, caption=test_caption, parse_mode="HTML")
        else:
            bot.send_message(CHANNEL_CHAT_ID, test_caption, parse_mode="HTML")
        bot.reply_to(message, "✅ ICT test signali kanalga muvaffaqiyatli yuborildi!")
    except Exception as e:
        try:
            bot.send_message(CHANNEL_CHAT_ID, test_caption, parse_mode="HTML")
            bot.reply_to(message, "✅ Test signali matn ko'rinishida kanalga yuborildi!")
        except Exception as err2:
            bot.reply_to(message, f"❌ Kanalga yuborishda xatolik: {err2}")

@bot.message_handler(func=lambda message: True)
def handle_trade_message(message):
    user_text = message.text
    user_id = message.from_user.id

    if user_id not in user_histories:
        user_histories[user_id] = []

    history_text = "\n".join(user_histories[user_id][-6:])

    prompt = (
        "Sen — Toshkentlik kripto-treyder do'stsan. Telegramda yaqin do'sting bilan chatlashyapsan.\n\n"
        "XARAKTERING VA USLUBING:\n"
        "- Jonli, hazilkash, biroz kinoyali, ko'cha tilida erkin gapir.\n"
        "- Gaplaring o'ta qisqa bo'lsin (bir necha so'z yoki bitta jumla).\n"
        "- 'Men ham dam olib uxlayman', 'Yaxshi, keyin gaplashamiz' degan robot gaplarni QAT'IYAN ISHLATMA!\n"
        "- Masalan: 'uxla' desa -> 'O'zing uxla brat, grafik qarab o'tiribman' yoki 'Bozor uxlamaydi, bizga dam yo'q' deb javob ber.\n"
        "- 'tur' desa -> 'Uyg'oqman, nima gap?' deb javob ber.\n"
        "- Bozor bo'yicha aniq signal bo'lmasa, o'zingdan yolg'on narx to'qima, 'Grafikni ko'rish kerak, hozircha noaniq' deb ayt.\n\n"
        f"Oldingi gaplar:\n{history_text}\n\n"
        f"Do'sting: {user_text}\n"
        "Sen:"
    )

    try:
        content = get_ai_analysis(prompt)
        if not content:
            bot.send_message(message.chat.id, "Ey jigar, tarmoqda tiqilinch bo'p qoldi, birozdan keyin yozvor.")
            return

        user_histories[user_id].append(f"Foydalanuvchi: {user_text}")
        user_histories[user_id].append(f"Sen: {content}")

        if content.startswith("SIGNAL_DETECTED"):
            clean_content = content.replace("SIGNAL_DETECTED", "").strip()
            title = user_text[:30]
            success, msg = save_trade_to_sheets(title, clean_content)

            if success:
                reply_text = f"🎯 *Signal Google Sheets'ga qadab qo'yildi, brat!*\n\n{clean_content}\n\n⚠️ _Kotletit qilib yuborma, risk-menejment esdan chiqmasin!_"
            else:
                reply_text = f"⚠️ Tahlil tayyor, lekin Sheets'ga saqlanmadi: {msg}\n\n{clean_content}"
            bot.send_message(message.chat.id, reply_text, parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, content)

    except Exception as e:
        bot.send_message(message.chat.id, f"Brat, xatolik berdi: {e}")

# --- 7. GOOGLE SHEETS MONITORING ---
sent_trade_ids = set()

def monitor_new_trades():
    global sent_trade_ids
    try:
        initial = get_trades_from_sheets()
        for t in initial:
            if t.get("id"):
                sent_trade_ids.add(t["id"])
    except Exception as e:
        print(f"Monitoring boshlanishida ogohlantirish: {e}")

    while True:
        try:
            time.sleep(60)
            trades = get_trades_from_sheets()
            for trade in trades:
                t_id = trade.get("id")
                if t_id and t_id not in sent_trade_ids:
                    title = trade.get("title", "Yangi Bitim")
                    content = trade.get("content", "")
                    signal_text = f"🚨 <b>YANGI SIGNAL / SAVDO!</b>\n\n📌 <b>{title}</b>\n\n{content}\n\n⚡️ <i>Menejment qoidalariga amal qiling!</i>"
                    try:
                        bot.send_message(CHANNEL_CHAT_ID, signal_text, parse_mode="HTML")
                        sent_trade_ids.add(t_id)
                    except Exception as err:
                        print(f"Kanalga yuborishda xatolik: {err}")
        except Exception as e:
            print(f"Monitoring davomida xatolik: {e}")

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
                    image_url = None
                    if "media_content" in latest and len(latest.media_content) > 0:
                        image_url = latest.media_content[0].get("url")
                    elif "enclosures" in latest and len(latest.enclosures) > 0:
                        image_url = latest.enclosures[0].get("url")

                    clean_summary = re.sub(r'<[^>]+>', '', raw_summary).strip()

                    prompt = f"""Sen Obsidian Lab tahliliy kripto kanali uchun post yozuvchi AI bo'lasan.
Quyidagi yangilikni o'zbek tiliga tarjima qilib, treyderlar uchun lo'nda va professional shaklda ber.

Sarlavha: {title}
Mazmuni: {clean_summary}

Format faqat mana shunday bo'lsin:
⚡️ *OBSIDIAN RADAR // MARKET ALERT*
━━━━━━━━━━━━━━━━━━━━

📌 *Mavzu:*
*[O'zbekcha qisqa sarlavha]*

📋 *Tafsilot:*
[Voqea haqida 2 jumlada asosiy mazmun]

💡 *Bozorga ta'siri:*
[Treydorlar uchun 1 ta xulosa]

━━━━━━━━━━━━━━━━━━━━
🌐 [Batafsil maqolani o'qish]({link})
"""
                    post_text = get_ai_analysis(prompt)

                    if not post_text:
                        post_text = f"⚡️ *OBSIDIAN RADAR // MARKET ALERT*\n\n📌 *Mavzu:* {title}\n\n📋 *Tafsilot:* {clean_summary[:200]}...\n\n🌐 [Batafsil maqola]({link})"

                    if image_url:
                        bot.send_photo(
                            chat_id=CHANNEL_CHAT_ID,
                            photo=image_url,
                            caption=post_text,
                            parse_mode="Markdown"
                        )
                    else:
                        bot.send_message(
                            chat_id=CHANNEL_CHAT_ID,
                            text=post_text,
                            parse_mode="Markdown",
                            disable_web_page_preview=True
                        )
                    
                    SEEN_NEWS.add(link)
                    print("LOG: [Obsidian Radar] Kanalga yangilik chiqdi!")

        except Exception as e:
            print(f"LOG: Yangiliklar tizimida xatolik: {e}")

        time.sleep(3600)

# --- 9. ISHGA TUSHIRISH ---
if __name__ == "__main__":
    t_flask = threading.Thread(target=run_flask, daemon=True)
    t_flask.start()

    t_sheet = threading.Thread(target=monitor_new_trades, daemon=True)
    t_sheet.start()

    t_news = threading.Thread(target=fetch_and_post_crypto_news, daemon=True)
    t_news.start()

    t_radar = threading.Thread(target=scan_and_post_ai_signals, daemon=True)
    t_radar.start()

    bot.infinity_polling()
