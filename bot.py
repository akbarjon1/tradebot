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
import secrets
from functools import wraps
import requests
import feedparser
import telebot
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, g
from google import genai
from google.genai import types as genai_types
from groq import Groq
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash


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
CORS(app)

# ===== PERSISTENT WEB APP LAYER =====
app.secret_key = get_env("SECRET_KEY") or hashlib.sha256(
    (TELEGRAM_BOT_TOKEN + ":obsidian-lab-session").encode()
).hexdigest()
DATABASE = get_env("DATABASE_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "obsidian_lab.sqlite3")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=get_env("COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)
app.permanent_session_lifetime = datetime.timedelta(days=30)

def db_conn():
    if "obs_db" not in g:
        g.obs_db = sqlite3.connect(DATABASE, timeout=20)
        g.obs_db.row_factory = sqlite3.Row
        g.obs_db.execute("PRAGMA foreign_keys=ON")
    return g.obs_db

def db_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def init_web_db():
    db_conn().executescript("""
    CREATE TABLE IF NOT EXISTS web_users(
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        username TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'USER',
        balance REAL NOT NULL DEFAULT 10000,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS web_trades(
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        asset TEXT NOT NULL,
        side TEXT NOT NULL,
        amount REAL NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL,
        pnl REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES web_users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS web_office(
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES web_users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS web_notifications(
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        read INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES web_users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS web_positions(
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        asset TEXT NOT NULL,
        side TEXT NOT NULL,
        size REAL NOT NULL,
        leverage INTEGER NOT NULL DEFAULT 10,
        entry_price REAL NOT NULL,
        tp REAL,
        sl REAL,
        opened_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES web_users(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_web_trades_user ON web_trades(user_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_web_office_user_kind ON web_office(user_id, kind);
    CREATE INDEX IF NOT EXISTS idx_web_notifications_user ON web_notifications(user_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_web_positions_user ON web_positions(user_id, opened_at);
    """)
    db_conn().commit()

def current_web_user():
    uid = session.get("web_uid")
    if not uid:
        return None
    row = db_conn().execute("SELECT * FROM web_users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None

def public_web_user(user):
    if not user:
        return None
    return {k:v for k,v in user.items() if k != "password"}

def require_web_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_web_user():
            return jsonify({"status":"error","message":"Sign in required"}), 401
        return fn(*args, **kwargs)
    return wrapped

def require_web_admin(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = current_web_user()
        if not user:
            return jsonify({"status":"error","message":"Sign in required"}), 401
        if user["role"] != "ADMIN":
            return jsonify({"status":"error","message":"Admin access required"}), 403
        return fn(*args, **kwargs)
    return wrapped

def require_csrf():
    if request.method in ("POST","PATCH","PUT","DELETE") and request.path.startswith("/api/") and request.path not in ("/api/update_balance",):
        token = request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf", "")
        return bool(expected and token and secrets.compare_digest(token, expected))
    return True

@app.before_request
def web_security():
    if request.path.startswith("/api/") and request.method in ("POST","PATCH","PUT","DELETE"):
        auth_bootstrap = request.path in ("/api/auth/login","/api/auth/register","/api/auth/reset-request","/api/auth/reset")
        if not auth_bootstrap and not require_csrf():
            return jsonify({"status":"error","message":"Security token expired. Refresh the page and try again."}), 403

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.teardown_appcontext
def close_web_db(exception=None):
    connection = g.pop("obs_db", None)
    if connection:
        connection.close()

with app.app_context():
    init_web_db()

WEBHOOK_BASE_URL = get_env("WEBHOOK_BASE_URL", get_env("RENDER_EXTERNAL_URL", "https://tradebot-xelo.onrender.com")).rstrip("/")
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

def render_app():
    return render_template(
        "index.html",
        title="Obsidian Lab — Build. Trade. Compete.",
        username=(current_web_user() or {}).get("username", "@trader"),
        api_base=request.url_root.rstrip("/")
    )

@app.route("/", methods=["GET"])
def home():
    return render_app()

@app.route("/markets", methods=["GET"])
@app.route("/markets/<symbol>", methods=["GET"])
@app.route("/trade", methods=["GET"])
@app.route("/leaderboard", methods=["GET"])
@app.route("/office", methods=["GET"])
@app.route("/profile", methods=["GET"])
@app.route("/profile/<uid>", methods=["GET"])
@app.route("/notifications", methods=["GET"])
@app.route("/settings", methods=["GET"])
@app.route("/login", methods=["GET"])
@app.route("/register", methods=["GET"])
@app.route("/forgot-password", methods=["GET"])
@app.route("/admin", methods=["GET"])
def website_page(symbol=None, uid=None):
    return render_app()

@app.route("/<path:path>", methods=["GET"])
def website_fallback(path):
    if path.startswith(("api/", "telegram/", "health")):
        return jsonify({"status":"error","message":"Not found"}), 404
    return render_app()

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

# ===== AUTH / ACCOUNT / OFFICE / ADMIN API =====
def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("JSON object required")
    return data

@app.route("/api/session", methods=["GET"])
def api_session():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(32)
    return jsonify({
        "status":"success",
        "user":public_web_user(current_web_user()),
        "csrf":session["csrf"]
    })

@app.route("/api/auth/register", methods=["POST"])
def api_register():
    key = request.remote_addr or "unknown"
    now_ts = time.time()
    recent = app.config.setdefault("_register_attempts", {})
    recent[key] = [t for t in recent.get(key, []) if t > now_ts - 60]
    if len(recent[key]) >= 6:
        return jsonify({"status":"error","message":"Too many registration attempts. Try again later."}), 429
    recent[key].append(now_ts)

    d = json_body()
    email = str(d.get("email","")).strip().lower()
    username = str(d.get("username","")).strip().lstrip("@")
    name = str(d.get("name") or username).strip()
    password = str(d.get("password",""))
    confirm = str(d.get("confirm") or d.get("password_confirm") or "")
    if "@" not in email or len(email) > 254:
        return jsonify({"status":"error","message":"Valid email required"}), 400
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        return jsonify({"status":"error","message":"Username must be 3–32 letters, numbers or underscore"}), 400
    if len(name) < 2 or len(name) > 100:
        return jsonify({"status":"error","message":"Valid name required"}), 400
    if len(password) < 10:
        return jsonify({"status":"error","message":"Password must contain at least 10 characters"}), 400
    if password != confirm:
        return jsonify({"status":"error","message":"Passwords do not match"}), 400
    user_id = uuid.uuid4().hex
    stamp = db_now()
    try:
        db_conn().execute(
            "INSERT INTO web_users VALUES(?,?,?,?,?,?,?,?,?)",
            (user_id,email,username,name,generate_password_hash(password),"USER",10000.0,stamp,stamp)
        )
        db_conn().commit()
    except sqlite3.IntegrityError:
        return jsonify({"status":"error","message":"Email or username already exists"}), 409
    return jsonify({"status":"success","message":"Account created. Sign in to continue."}), 201

def find_user_in_google_sheet(email):
    """Return an existing account from the Users worksheet, if configured."""
    global spreadsheet
    if not spreadsheet:
        return None
    try:
        ws = spreadsheet.worksheet("Users")
        rows = ws.get_all_values()
        if not rows:
            return None
        headers = [str(h).strip().lower() for h in rows[0]]
        aliases = {
            "id": ("user id", "userid", "id"),
            "email": ("email", "e-mail"),
            "username": ("username", "user name", "handle"),
            "password": ("password hash", "password", "password_hash"),
            "balance": ("balance",),
            "name": ("full name", "name", "fullname"),
        }
        indexes = {}
        for key, options in aliases.items():
            indexes[key] = next((headers.index(x) for x in options if x in headers), None)
        if indexes["email"] is None or indexes["password"] is None:
            return None
        for row in rows[1:]:
            def value(key, default=""):
                idx = indexes.get(key)
                return str(row[idx]).strip() if idx is not None and idx < len(row) else default
            if value("email").lower() != email:
                continue
            user_id = value("id") or uuid.uuid4().hex
            username = value("username") or email.split("@", 1)[0]
            name = value("name") or username
            hashed = value("password")
            try:
                balance = float(value("balance", "10000") or 10000)
                if balance <= 0:
                    balance = 10000.0
            except (TypeError, ValueError):
                balance = 10000.0
            return {
                "id": user_id, "email": email, "username": username,
                "name": name, "password": hashed, "role": "USER",
                "balance": balance, "created_at": db_now(), "updated_at": db_now()
            }
    except Exception as err:
        print(f"Google Sheets Users login lookup failed: {err}")
    return None


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    d = json_body()
    email = str(d.get("email","")).strip().lower()
    password = str(d.get("password",""))
    user = db_conn().execute("SELECT * FROM web_users WHERE email=?", (email,)).fetchone()

    # Render may start with a fresh local SQLite database. Restore a matching
    # existing user from the connected Users worksheet before rejecting login.
    if not user:
        sheet_user = find_user_in_google_sheet(email)
        if sheet_user and check_password_hash(sheet_user["password"], password):
            try:
                db_conn().execute(
                    "INSERT INTO web_users (id,email,username,name,password,role,balance,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    tuple(sheet_user[k] for k in ("id","email","username","name","password","role","balance","created_at","updated_at"))
                )
                db_conn().commit()
                user = db_conn().execute("SELECT * FROM web_users WHERE email=?", (email,)).fetchone()
            except sqlite3.IntegrityError:
                # Resolve any partial restore/duplicate row by looking up email again.
                user = db_conn().execute("SELECT * FROM web_users WHERE email=?", (email,)).fetchone()

    if not user or not check_password_hash(user["password"], password):
        return jsonify({"status":"error","message":"Invalid email or password"}), 401
    session.clear()
    session["web_uid"] = user["id"]
    session["csrf"] = secrets.token_hex(32)
    # Web terminalda login 30 kun saqlanadi; page reload/renderdan keyin qayta register shart emas.
    session.permanent = True
    return jsonify({"status":"success","user":public_web_user(dict(user)),"csrf":session["csrf"]})

@app.route("/api/auth/logout", methods=["POST"])
@require_web_auth
def api_logout():
    session.clear()
    return jsonify({"status":"success"})


# ===== SHARED PAPER TRADING STATE (MULTI-POSITION SUPPORT) =====
@app.route("/api/paper/state", methods=["GET"])
@require_web_auth
def paper_state():
    user = current_web_user()
    c = db_conn()
    pos_rows = c.execute(
        "SELECT * FROM web_positions WHERE user_id=? ORDER BY opened_at DESC", (user["id"],)
    ).fetchall()
    trades = c.execute(
        "SELECT * FROM web_trades WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
        (user["id"],)
    ).fetchall()

    positions = []
    for pos in pos_rows:
        positions.append({
            "id": pos["id"],
            "symbol": pos["asset"].replace("USDT", ""),
            "pair": pos["asset"],
            "type": pos["side"],
            "size": float(pos["size"]),
            "leverage": int(pos["leverage"]),
            "entryPrice": float(pos["entry_price"]),
            "tp": float(pos["tp"]) if pos["tp"] is not None else None,
            "sl": float(pos["sl"]) if pos["sl"] is not None else None,
            "openedAt": pos["opened_at"]
        })

    return jsonify({
        "status": "success",
        "account_id": str(user["id"]),
        "balance": float(user["balance"]),
        "positions": positions,
        "position": positions[0] if positions else None,
        "trades": [dict(x) for x in trades]
    })


@app.route("/api/paper/open", methods=["POST"])
@require_web_auth
def paper_open():
    user = current_web_user()
    d = json_body()

    asset = str(d.get("pair") or d.get("asset") or "BTCUSDT").upper().strip()
    side = str(d.get("side") or d.get("type") or "LONG").upper().strip()
    amount = float(d.get("amount", 0) or 0)
    leverage = int(d.get("leverage", 10) or 10)
    entry = float(d.get("entryPrice", 0) or 0)
    tp = d.get("tp")
    sl = d.get("sl")
    tp = float(tp) if tp not in (None, "", False) else None
    sl = float(sl) if sl not in (None, "", False) else None

    if side not in {"LONG", "SHORT"}:
        return jsonify({"status":"error","message":"Invalid side"}), 400
    if not re.fullmatch(r"[A-Z0-9]{3,20}", asset):
        return jsonify({"status":"error","message":"Invalid asset"}), 400
    if amount < 10:
        return jsonify({"status":"error","message":"Minimum order is $10"}), 400
    if leverage not in {1, 5, 10, 20}:
        return jsonify({"status":"error","message":"Invalid leverage"}), 400
    if entry <= 0:
        return jsonify({"status":"error","message":"Invalid entry price"}), 400
    c = db_conn()
    stamp = db_now()
    pos_id = uuid.uuid4().hex
    trade_id = uuid.uuid4().hex

    # Balance check + deduction atomically: PC va telefon bir vaqtda order yuborsa
    # ham balans minusga ketmaydi.
    updated = c.execute(
        "UPDATE web_users SET balance=balance-?,updated_at=? WHERE id=? AND balance>=?",
        (amount, stamp, user["id"], amount)
    )
    if updated.rowcount != 1:
        c.rollback()
        return jsonify({"status":"error","message":"Insufficient balance"}), 400

    c.execute(
        """INSERT INTO web_positions
           (id,user_id,asset,side,size,leverage,entry_price,tp,sl,opened_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (pos_id, user["id"], asset, side, amount, leverage, entry, tp, sl, stamp)
    )
    c.execute(
        """INSERT INTO web_trades
           (id,user_id,asset,side,amount,entry_price,status,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (trade_id, user["id"], asset, side, amount, entry, "OPEN", stamp)
    )
    c.commit()
    fresh = c.execute("SELECT balance FROM web_users WHERE id=?", (user["id"],)).fetchone()

    return jsonify({
        "status":"success",
        "balance":float(fresh["balance"]),
        "position":{
            "id":pos_id,
            "symbol":asset.replace("USDT",""),
            "pair":asset,
            "type":side,
            "size":amount,
            "leverage":leverage,
            "entryPrice":entry,
            "tp":tp,
            "sl":sl,
            "openedAt":stamp
        }
    }), 201


@app.route("/api/paper/migrate", methods=["POST"])
@require_web_auth
def paper_migrate():
    user = current_web_user()
    d = json_body()
    p = d.get("position")
    if not isinstance(p, dict):
        return jsonify({"status":"error","message":"Position required"}), 400

    c = db_conn()
    try:
        asset = str(p.get("pair") or p.get("symbol","BTC") + "USDT").upper()
        side = str(p.get("type","LONG")).upper()
        size = float(p.get("size",0))
        leverage = int(p.get("leverage",10))
        entry = float(p.get("entryPrice",0))
        tp = p.get("tp")
        sl = p.get("sl")
        tp = float(tp) if tp not in (None,"",False) else None
        sl = float(sl) if sl not in (None,"",False) else None
        opened = str(p.get("openedAt") or db_now())
        if side not in {"LONG","SHORT"} or size <= 0 or entry <= 0:
            raise ValueError
    except Exception:
        return jsonify({"status":"error","message":"Invalid position"}), 400

    pos_id = uuid.uuid4().hex
    trade_id = uuid.uuid4().hex
    if size > float(user["balance"]):
        return jsonify({"status":"error","message":"Insufficient balance for migration"}), 400

    stamp = db_now()
    c.execute(
        """INSERT INTO web_positions
           (id,user_id,asset,side,size,leverage,entry_price,tp,sl,opened_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (pos_id, user["id"], asset, side, size, leverage, entry, tp, sl, opened)
    )
    c.execute(
        "UPDATE web_users SET balance=balance-?,updated_at=? WHERE id=?",
        (size, stamp, user["id"])
    )
    c.execute(
        """INSERT INTO web_trades
           (id,user_id,asset,side,amount,entry_price,status,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (trade_id, user["id"], asset, side, size, entry, "OPEN", stamp)
    )
    c.commit()
    return jsonify({"status":"success"})


@app.route("/api/paper/close", methods=["POST"])
@require_web_auth
def paper_close():
    user = current_web_user()
    d = json_body()
    live = float(d.get("livePrice", 0) or 0)
    pos_id = str(d.get("id", "")).strip()

    if live <= 0:
        return jsonify({"status":"error","message":"Invalid live price"}), 400

    c = db_conn()
    if pos_id:
        pos = c.execute("SELECT * FROM web_positions WHERE id=? AND user_id=?", (pos_id, user["id"])).fetchone()
    else:
        pos = c.execute("SELECT * FROM web_positions WHERE user_id=? ORDER BY opened_at DESC LIMIT 1", (user["id"],)).fetchone()

    if not pos:
        return jsonify({"status":"error","message":"No open position"}), 404

    diff = (live - float(pos["entry_price"])) / float(pos["entry_price"])
    if pos["side"] == "SHORT":
        diff = -diff
    pnl = float(pos["size"]) * diff * int(pos["leverage"])
    credit = max(0.0, float(pos["size"]) + pnl)
    stamp = db_now()

    # Aynan shu positionga tegishli OPEN trade'ni yopamiz.
    # Bir xil symbol/side bilan bir nechta position bo'lishi mumkin.
    trade_row = c.execute(
        """SELECT id FROM web_trades
           WHERE user_id=? AND asset=? AND side=? AND amount=?
             AND entry_price=? AND status='OPEN' AND created_at=?
           ORDER BY created_at ASC LIMIT 1""",
        (user["id"], pos["asset"], pos["side"], float(pos["size"]),
         float(pos["entry_price"]), pos["opened_at"])
    ).fetchone()
    if trade_row:
        c.execute(
            "UPDATE web_trades SET exit_price=?,pnl=?,status='CLOSED' WHERE id=?",
            (live, pnl, trade_row["id"])
        )

    c.execute(
        "UPDATE web_users SET balance=balance+?,updated_at=? WHERE id=?",
        (credit, stamp, user["id"])
    )
    c.execute("DELETE FROM web_positions WHERE id=? AND user_id=?", (pos["id"], user["id"]))
    c.commit()

    fresh = c.execute("SELECT balance FROM web_users WHERE id=?", (user["id"],)).fetchone()
    return jsonify({
        "status":"success",
        "balance":float(fresh["balance"]),
        "closed_id": pos["id"],
        "pnl":pnl
    })

@app.route("/api/account", methods=["GET"])
@require_web_auth
def api_account():
    user = current_web_user()
    trades = [dict(r) for r in db_conn().execute(
        "SELECT * FROM web_trades WHERE user_id=? ORDER BY created_at DESC",(user["id"],)
    )]
    return jsonify({
        "status":"success",
        "balance":float(user["balance"]),
        "trades":trades,
        "user":public_web_user(user)
    })

@app.route("/api/settings/profile", methods=["POST"])
@require_web_auth
def api_settings_profile():
    user=current_web_user(); d=json_body()
    name=str(d.get("name","")).strip()
    if not name or len(name)>100:
        return jsonify({"status":"error","message":"Valid name required"}),400
    db_conn().execute("UPDATE web_users SET name=?,updated_at=? WHERE id=?",(name,db_now(),user["id"]))
    db_conn().commit()
    return jsonify({"status":"success","user":public_web_user(current_web_user())})

@app.route("/api/settings/security", methods=["POST"])
@require_web_auth
def api_settings_security():
    user=current_web_user(); d=json_body()
    if not check_password_hash(user["password"],str(d.get("current",""))):
        return jsonify({"status":"error","message":"Current password is incorrect"}),400
    new=str(d.get("password",""))
    if len(new)<10:
        return jsonify({"status":"error","message":"New password must contain at least 10 characters"}),400
    db_conn().execute("UPDATE web_users SET password=?,updated_at=? WHERE id=?",(generate_password_hash(new),db_now(),user["id"]))
    db_conn().commit()
    return jsonify({"status":"success"})

@app.route("/api/office/<kind>", methods=["GET","POST"])
@require_web_auth
def api_office(kind):
    allowed={"tasks","projects","team","documents","calendar"}
    if kind not in allowed:
        return jsonify({"status":"error","message":"Unknown office section"}),404
    user=current_web_user()
    if request.method=="GET":
        rows=db_conn().execute(
            "SELECT * FROM web_office WHERE user_id=? AND kind=? ORDER BY updated_at DESC",(user["id"],kind)
        ).fetchall()
        return jsonify({"status":"success","items":[{**json.loads(r["data"]),"id":r["id"],"updatedAt":r["updated_at"]} for r in rows]})
    d=json_body()
    if len(json.dumps(d,ensure_ascii=False))>500000:
        return jsonify({"status":"error","message":"Record is too large"}),400
    title=str(d.get("title","")).strip()
    if not title:
        return jsonify({"status":"error","message":"Title is required"}),400
    rid=uuid.uuid4().hex; stamp=db_now()
    db_conn().execute("INSERT INTO web_office VALUES(?,?,?,?,?,?)",(rid,user["id"],kind,json.dumps(d,ensure_ascii=False),stamp,stamp))
    db_conn().commit()
    return jsonify({"status":"success","id":rid,"item":{**d,"id":rid,"updatedAt":stamp}}),201

@app.route("/api/office/<kind>/<rid>", methods=["PATCH","DELETE"])
@require_web_auth
def api_office_item(kind,rid):
    if kind not in {"tasks","projects","team","documents","calendar"}:
        return jsonify({"status":"error","message":"Unknown office section"}),404
    user=current_web_user()
    row=db_conn().execute("SELECT * FROM web_office WHERE id=? AND user_id=? AND kind=?",(rid,user["id"],kind)).fetchone()
    if not row: return jsonify({"status":"error","message":"Record not found"}),404
    if request.method=="DELETE":
        db_conn().execute("DELETE FROM web_office WHERE id=?",(rid,));db_conn().commit()
        return jsonify({"status":"success"})
    d=json_body(); current=json.loads(row["data"]); current.update(d); stamp=db_now()
    db_conn().execute("UPDATE web_office SET data=?,updated_at=? WHERE id=?",(json.dumps(current,ensure_ascii=False),stamp,rid));db_conn().commit()
    return jsonify({"status":"success","item":{**current,"id":rid,"updatedAt":stamp}})

@app.route("/api/notifications", methods=["GET"])
@require_web_auth
def api_notifications():
    user=current_web_user()
    rows=db_conn().execute("SELECT * FROM web_notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",(user["id"],)).fetchall()
    return jsonify({"status":"success","items":[dict(r) for r in rows]})

@app.route("/api/notifications/<nid>/read", methods=["POST"])
@require_web_auth
def api_notification_read(nid):
    user=current_web_user()
    db_conn().execute("UPDATE web_notifications SET read=1 WHERE id=? AND user_id=?",(nid,user["id"]));db_conn().commit()
    return jsonify({"status":"success"})

@app.route("/api/integrations", methods=["GET"])
@require_web_auth
def api_integrations():
    return jsonify({"status":"success","items":[
        {"name":"Telegram","status":"CONNECTED" if TELEGRAM_BOT_TOKEN else "NOT CONFIGURED"},
        {"name":"Google Sheets","status":"CONNECTED" if spreadsheet else "NOT CONFIGURED"},
        {"name":"Gemini","status":"CONNECTED" if gemini_client else "NOT CONFIGURED"},
        {"name":"Groq","status":"CONNECTED" if groq_client else "NOT CONFIGURED"}
    ]})

@app.route("/api/admin/overview", methods=["GET"])
@require_web_admin
def api_admin_overview():
    c=db_conn()
    users=c.execute("SELECT count(*) FROM web_users").fetchone()[0]
    trades=c.execute("SELECT count(*) FROM web_trades").fetchone()[0]
    volume=c.execute("SELECT COALESCE(SUM(amount),0) FROM web_trades").fetchone()[0]
    return jsonify({"status":"success","users":users,"trades":trades,"volume":float(volume),"markets":8})

@app.route("/api/admin/users", methods=["GET"])
@require_web_admin
def api_admin_users():
    rows=db_conn().execute("SELECT id,email,username,name,role,balance,created_at FROM web_users ORDER BY created_at DESC").fetchall()
    return jsonify({"status":"success","items":[dict(r) for r in rows]})

@app.route('/api/update_balance', methods=['POST'])
def update_balance():
    global spreadsheet
    try:
        data = request.get_json(force=True) or {}
        user_id = str(data.get("user_id", "")).strip()
        username = str(data.get("username", "Trader")).strip().lstrip("@")[:32]
        balance = float(data.get("balance", 10000.0))
        if not user_id or not re.fullmatch(r"[A-Za-z0-9_:@.-]{2,100}", user_id):
            return jsonify({"status": "error", "message": "Noto'g'ri user ID"}), 400
        if not (0 <= balance <= 1e9):
            return jsonify({"status":"error","message":"Invalid balance"}),400

        stamp=db_now()
        row=db_conn().execute("SELECT id FROM web_users WHERE id=?", (user_id,)).fetchone()
        if row:
            db_conn().execute("UPDATE web_users SET username=?,balance=?,updated_at=? WHERE id=?",(username or "trader",balance,stamp,user_id))
        else:
            email=f"{user_id[:60]}@telegram.local"
            try:
                db_conn().execute(
                    "INSERT INTO web_users VALUES(?,?,?,?,?,?,?,?,?)",
                    (user_id,email,username or "trader",username or "Trader",generate_password_hash(secrets.token_hex(16)),"USER",balance,stamp,stamp)
                )
            except sqlite3.IntegrityError:
                pass
        db_conn().commit()

        if spreadsheet:
            try:
                ws = spreadsheet.worksheet("Leaderboard")
                all_vals = ws.get_all_values()
                row_to_update = None
                for i, row in enumerate(all_vals[1:], start=2):
                    if len(row) >= 1 and row[0].strip() == user_id:
                        row_to_update = i; break
                formatted_bal = f"{balance:.2f}"
                if row_to_update:
                    ws.update_cell(row_to_update, 2, username or "Trader")
                    ws.update_cell(row_to_update, 3, formatted_bal)
                else:
                    ws.append_row([user_id, username or "Trader", formatted_bal])
            except Exception as sheet_err:
                print(f"Sheets sync warning: {sheet_err}")
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Xatolik update_balance: {e}")
        return jsonify({"status": "error", "message": "Balance sync failed"}), 500


@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    global spreadsheet
    try:
        if not spreadsheet:
            rows=[]
            for u in db_conn().execute("SELECT id,username,balance FROM web_users"):
                rows.append({
                    "User ID":u["id"],
                    "Username":"@"+str(u["username"]).lstrip("@"),
                    "Balance":float(u["balance"])
                })
            rows.sort(key=lambda x:x["Balance"], reverse=True)
            return jsonify({"status":"success","leaders":rows})
        try:
            ws = spreadsheet.worksheet("Leaderboard")
        except Exception:
            return jsonify({"status":"success","leaders":[]})
        records = ws.get_all_records()
        valid_leaders=[]
        for r in records:
            raw_bal=str(r.get("Balance","0")).replace(" ","").replace("\xa0","").replace(",",".")
            try: bal_val=float(raw_bal)
            except Exception: bal_val=0.0
            valid_leaders.append({
                "User ID":str(r.get("User ID","")),
                "Username":str(r.get("Username","Trader")),
                "Balance":bal_val
            })
        sorted_leaders=sorted(valid_leaders,key=lambda x:x["Balance"],reverse=True)[:10]
        return jsonify({"status":"success","leaders":sorted_leaders})
    except Exception as e:
        print(f"Leaderboard olishda xatolik: {e}")
        return jsonify({"status":"error","message":"Leaderboard unavailable"}),500


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

    while True:
        time.sleep(3600)
