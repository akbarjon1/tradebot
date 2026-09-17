"""
Obsidian Lab — Telegram bot + Flask API (paper-trading terminal, ICT/SMC signal
skaneri, AI NPC suhbatlari va HQ Office live-status backend).

Kerakli environment o'zgaruvchilari (Render/Railway/server sozlamalarida):
    TELEGRAM_BOT_TOKEN     — MAJBURIY. BotFather'dan olingan token.
    GEMINI_API_KEY         — AI javoblar uchun (asosiy).
    GROQ_API_KEY           — AI javoblar uchun (zaxira, Gemini ishlamasa).
    SPREADSHEET_ID         — Google Sheets jadval ID (savdolar/leaderboard).
    GOOGLE_CREDENTIALS_JSON— Google service-account JSON (bitta qatorda).
    CHANNEL_CHAT_ID        — Signal/yangiliklar chiqadigan kanal (masalan @obsidian_lab_uz).
    ADMIN_CHAT_ID          — Web-saytdagi feedback shu chatga tushadi (bo'sh bo'lsa CHANNEL_CHAT_ID).
    ADMIN_USER_IDS         — /test_signal kabi buyruqlarga ruxsat berilgan Telegram user ID'lari,
                              vergul bilan ajratilgan, masalan "123456789,987654321".
                              Bo'sh qoldirilsa, buyruq hech kimga cheklanmagan holda ochiq qoladi.
"""

import os
import re
import time
import json
import uuid
import datetime
import threading
import hashlib
import sqlite3
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import feedparser
import telebot
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
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
GEMINI_API_KEY = get_env("GEMINI_API_KEY")
GROQ_API_KEY = get_env("GROQ_API_KEY")
SPREADSHEET_ID = get_env("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = get_env("GOOGLE_CREDENTIALS_JSON")
CHANNEL_CHAT_ID = get_env("CHANNEL_CHAT_ID", "@obsidian_lab_uz")
# Feedback/izohlar shu chatga tushadi (admin guruh yoki shaxsiy chat ID). Bo'sh bo'lsa CHANNEL_CHAT_ID ishlatiladi.
ADMIN_CHAT_ID = get_env("ADMIN_CHAT_ID", CHANNEL_CHAT_ID)
# /test_signal kabi admin buyruqlarini faqat shu Telegram user ID'lariga ruxsat berish uchun (vergul bilan: "123,456")
ADMIN_USER_IDS = {uid.strip() for uid in get_env("ADMIN_USER_IDS").split(",") if uid.strip()}

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN o'rnatilmagan! Uni Render/Railway/server muhitida "
        "environment variable sifatida qo'shing. Eski token kodda ochiq yozilgan edi — "
        "u allaqachon BotFather orqali /revoke qilinishi va yangisi bilan almashtirilishi SHART, "
        "chunki fayl istalgan joyga (masalan GitHub'ga) tushgan bo'lsa, u token ochiq qolgan."
    )

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
app.secret_key = os.getenv("FLASK_SECRET_KEY", "obsidian-lab-change-this-secret")
DB_PATH = os.getenv("OBSIDIAN_DB", "obsidian_lab.db")
CORS(app)

# Render uchun Telegram Webhook.
# getUpdates/long-polling o'rniga webhook ishlatamiz — 409 Conflict yo'qoladi.
WEBHOOK_BASE_URL = get_env("WEBHOOK_BASE_URL", get_env("RENDER_EXTERNAL_URL", "https://tradebot-xelo.onrender.com")).rstrip("/")
# Tokenni URL ichida ochiq ko'rsatmaslik uchun deterministik secret path.
WEBHOOK_SECRET = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode("utf-8")).hexdigest()[:40]
WEBHOOK_PATH = f"/telegram/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"

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
comments_store = []

@app.route(WEBHOOK_PATH, methods=['POST'])
def telegram_webhook():
    try:
        update_json = request.get_data(as_text=True)
        if update_json:
            update = telebot.types.Update.de_json(update_json)
            bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        print(f"⚠️ Telegram webhook update xatosi: {e}")
        return "OK", 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "telegram": "webhook", "service": "obsidian-lab"}), 200

@app.route('/', methods=['GET'])
def home():
    return render_template('home.html', title='Obsidian Lab — Build. Trade. Compete.', username=session.get('username','@trader'))


# ===== OBSIDIAN LAB MULTI-PAGE SITE =====
def db():
    conn=sqlite3.connect(DB_PATH)
    conn.row_factory=sqlite3.Row
    return conn

def init_db():
    conn=db(); c=conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'USER', created_at TEXT NOT NULL)""")
    conn.commit(); conn.close()
init_db()

def bootstrap_admin():
    email=os.getenv('ADMIN_EMAIL','').strip().lower(); pw=os.getenv('ADMIN_PASSWORD','')
    if not email or not pw: return
    conn=db(); row=conn.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
    if not row:
        username=os.getenv('ADMIN_USERNAME','admin')
        try: conn.execute('INSERT INTO users(name,username,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)',('Obsidian Admin',username,email,generate_password_hash(pw),'ADMIN',datetime.datetime.utcnow().isoformat())); conn.commit()
        except sqlite3.IntegrityError: pass
    else: conn.execute("UPDATE users SET role='ADMIN' WHERE email=?",(email,)); conn.commit()
    conn.close()
bootstrap_admin()

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'): return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('role')!='ADMIN': return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped

@app.route('/markets')
def markets_page(): return render_template('markets.html', title='Markets — Obsidian Lab')
@app.route('/markets/<symbol>')
def market_detail(symbol): return render_template('market_detail.html', symbol=symbol.upper(), title=f'{symbol.upper()} — Obsidian Lab')
@app.route('/trade')
@login_required
def trade_page(): return render_template('trade.html', title='Trade — Obsidian Lab', username=session.get('username','@trader'))
@app.route('/leaderboard')
def leaderboard_page(): return render_template('leaderboard.html', title='Leaderboard — Obsidian Lab')
@app.route('/profile')
@login_required
def profile_page(): return render_template('profile.html', title='Profile — Obsidian Lab', username=session.get('username','@trader'))
@app.route('/profile/<user_id>')
def public_profile(user_id): return render_template('profile.html', title='Profile — Obsidian Lab', username=session.get('username','@trader'), profile_id=user_id)
@app.route('/web-office')
@login_required
def office_page(): return render_template('office.html', title='Web Office — Obsidian Lab', section='overview')
@app.route('/web-office/<section>')
@login_required
def office_section(section):
    allowed={'tasks','projects','team','documents','calendar','analytics'}
    if section not in allowed: return redirect(url_for('office_page'))
    return render_template('office.html', title=f'{section.title()} — Web Office', section=section)
@app.route('/notifications')
@login_required
def notifications_page(): return render_template('notifications.html', title='Notifications — Obsidian Lab')
@app.route('/settings')
@login_required
def settings_page(): return render_template('settings.html', title='Settings — Obsidian Lab', section='profile')
@app.route('/settings/<section>')
@login_required
def settings_section(section):
    allowed={'profile','security','notifications','integrations','appearance'}
    if section not in allowed: return redirect(url_for('settings_page'))
    return render_template('settings.html', title=f'{section.title()} — Settings', section=section)
@app.route('/login', methods=['GET','POST'])
def login():
    error=None
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); pw=request.form.get('password','')
        conn=db(); user=conn.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone(); conn.close()
        if user and check_password_hash(user['password_hash'],pw):
            session.update(user_id=user['id'],username=user['username'],role=user['role'])
            return redirect(request.args.get('next') or url_for('home'))
        error='Email yoki parol noto‘g‘ri.'
    return render_template('login.html', title='Sign in — Obsidian Lab', error=error)
@app.route('/register', methods=['GET','POST'])
def register():
    error=None
    if request.method=='POST':
        name=request.form.get('name','').strip(); username=request.form.get('username','').strip(); email=request.form.get('email','').strip().lower(); pw=request.form.get('password',''); cp=request.form.get('confirm_password','')
        if not name or not username or not email or len(pw)<6: error='Barcha maydonlarni to‘ldiring. Parol kamida 6 belgidan iborat.'
        elif pw!=cp: error='Parollar mos emas.'
        else:
            try:
                conn=db(); cur=conn.execute('INSERT INTO users(name,username,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)',(name,username,email,generate_password_hash(pw),'USER',datetime.datetime.utcnow().isoformat())); conn.commit(); uid=cur.lastrowid; conn.close(); session.update(user_id=uid,username='@'+username.lstrip('@'),role='USER'); return redirect(url_for('home'))
            except sqlite3.IntegrityError: error='Username yoki email allaqachon mavjud.'
    return render_template('register.html', title='Create account — Obsidian Lab', error=error)
@app.route('/forgot-password', methods=['GET','POST'])
def forgot_password(): return render_template('forgot.html', title='Reset password — Obsidian Lab', sent=request.method=='POST')
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

@app.route('/admin')
@admin_required
def admin_home(): return render_template('admin.html', title='Admin — Obsidian Lab', section='overview')
@app.route('/admin/<section>')
@admin_required
def admin_section(section): return render_template('admin.html', title=f'Admin {section.title()} — Obsidian Lab', section=section)

@app.route('/api/price')
def api_price():
    symbol=request.args.get('symbol','BTCUSDT').upper()
    if not re.fullmatch(r'[A-Z0-9]{5,15}',symbol): return jsonify(status='error',message='Invalid symbol'),400
    for host in ('https://api.binance.com','https://api1.binance.com','https://api2.binance.com','https://api3.binance.com','https://data-api.binance.vision'):
        try:
            r=requests.get(host+'/api/v3/ticker/price',params={'symbol':symbol},timeout=4,headers={'User-Agent':'ObsidianLab/1.0'})
            d=r.json(); p=float(d.get('price',0))
            if p>0:return jsonify(status='success',symbol=symbol,price=p,source='BINANCE')
        except Exception: pass
    return jsonify(status='error',message='Market data unavailable'),503

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
            bot.send_message(ADMIN_CHAT_ID, tg_text, parse_mode="Markdown")
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
    """Izometrik HQ ofisdagi Live Ticker va statuslar uchun jonli ma'lumotlar"""
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
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

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
    if ADMIN_USER_IDS and str(message.from_user.id) not in ADMIN_USER_IDS:
        bot.reply_to(message, "⛔️ Bu buyruq faqat adminlar uchun.")
        return

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
        # Xotirada cheksiz o'sib ketmasligi uchun oxirgi 20 ta yozuvni saqlaymiz
        user_histories[user_id] = user_histories[user_id][-20:]

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

    # Polling o'rniga Telegram Webhook. Bu getUpdates 409 Conflict muammosini
    # bartaraf qiladi va Render uchun barqarorroq ishlaydi.
    try:
        bot.remove_webhook()
        time.sleep(1)
        webhook_result = bot.set_webhook(
            url=WEBHOOK_URL,
            drop_pending_updates=True
        )
        print(f"✅ Telegram Webhook o'rnatildi: {WEBHOOK_URL}")
        print(f"✅ Telegram webhook result: {webhook_result}")
    except Exception as e:
        print(f"❌ Telegram webhook o'rnatilmadi: {e}")

    # Flask server daemon threadda ishlaydi; processni tirik ushlab turamiz.
    while True:
        time.sleep(3600)
