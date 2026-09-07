import os
import re
import time
import json
import uuid
import datetime
import threading
import feedparser
import telebot
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, render_template, request
from google import genai
from google.genai import types as genai_types
from groq import Groq

# --- 1. SOZLAMALAR VA KALITLAR ---
def get_env(key, default=""):
    val = os.environ.get(key, default)
    return val.strip() if val else default

TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", "6722502116:AAGMwQ0EOyYIyGDvpfAB2J9sygrO5yy_DVo")
GEMINI_API_KEY = get_env("GEMINI_API_KEY")
GROQ_API_KEY = get_env("GROQ_API_KEY")
SPREADSHEET_ID = get_env("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = get_env("GOOGLE_CREDENTIALS_JSON")
CHANNEL_CHAT_ID = get_env("CHANNEL_CHAT_ID", "@obsidian_lab_uz")

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
        requests = [
            # 1. Shapkani muzlatish (Freeze header)
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1}
                    },
                    "fields": "gridProperties.frozenRowCount"
                }
            },
            # 2. Ustunlar kengligini to'g'irlash (A: 90px, B: 240px, C: 480px, D: 110px)
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
            # 3. Shapka dizayni: To'q qora-ko'k fon, oq qalin shrift, o'rtada
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
            # 4. Matnlarni sig'dirish (Text Wrap) va vertikal markazlashtirish
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
        sp.batch_update({"requests": requests})
        print("🎨 Google Sheets dizayni avtomatik ravishda bezatildi!")
    except Exception as e:
        print(f"Dizayn qo'llashda ogohlantirish: {e}")

if GOOGLE_CREDENTIALS_JSON and SPREADSHEET_ID:
    try:
        cred_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(cred_info, scopes=scopes)
        gc = gspread.authorize(credentials)
        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.sheet1
        print("✅ Google Sheets ulandi!")
        
        # Jadvalni bir martada to'liq go'zal ko'rinishga keltiramiz:
        format_google_sheet(sheet, spreadsheet)
    except Exception as e:
        print(f"⚠️ Google Sheets ulanishda xatolik: {e}")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)

# --- 2. UNIVERSAL AI TAHLIL FUNKSIYASI ---
def get_ai_analysis(prompt: str) -> str:
    if gemini_client:
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.65    ,
                    max_output_tokens=800,
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
                temperature=0.7,
                max_tokens=800,
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

# --- 4. FLASK WEB SAYTI ---
CHANNEL_ID = "-5436696482"
comments_store = []

@app.route('/')
def home():
    trades = get_trades_from_sheets()
    return render_template('index.html', trades=trades, comments=comments_store)

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
                f"┌ 💬 *OBSIDIAN LAB // FEEDBACK*\n"
                f"├ ⏱ *Vaqt:* `{now_time}`\n"
                f"├ 👤 *Manba:* `Web Terminal`\n"
                f"└ ────────────────────\n\n"
                f"📝 *Fikr / Izoh:*\n"
                f"« {user_comment} »\n\n"
                f"▫️ _Status: Qabul qilindi_"
            )
            bot.send_message(CHANNEL_ID, tg_text, parse_mode="Markdown")
        except Exception as e:
            print(f"Kanalga yuborishda xatolik: {e}")
    return home()

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- 5. TELEGRAM BOT HANDLERLAR ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(message.chat.id, "Salom bratva! Obsidian Radar yonizda. Bozor qon yig'layaptimi yo yashil shamlar bormi? Signal bo'lsa tashlang bazaga tiqamiz, savol bo'lsa bemalol — gaplashamiz!")

@bot.message_handler(func=lambda message: True)
def handle_trade_message(message):
    user_text = message.text
    
    prompt = f"""Sen telegramdagi do'stsan. O'zbek tilida erkin, tabiiy, hazilkash va lo'nda gapirasan. 
Oldingi gaplarni qaytaraverma, to'tiqush bo'lma. Xuddi o'rtog'ing bilan gaplashayotgandek 1 ta gap bilan javob ber.

Foydalanuvchi: {user_text}
Javob:"""
    try:
        content = get_ai_analysis(prompt)
        if not content:
            bot.send_message(message.chat.id, "Ey jigar, tarmoqda tiqilinch bo'p qoldi, birozdan keyin yozvor.")
            return

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

# --- 6. GOOGLE SHEETS MONITORING ---
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

# --- 7. AVTOMATIK YANGILIKLAR TIZIMI ---
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

Format faqat mana shunday bo'lsin (Telegram rasm ostiga sig'ishi uchun 700 belgidan oshmasin):
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
                    print("LOG: [Obsidian Radar] Kanalga post chiqdi!")

        except Exception as e:
            print(f"LOG: Yangiliklar tizimida xatolik: {e}")

        time.sleep(3600)

# --- 8. ISHGA TUSHIRISH ---
if __name__ == "__main__":
    t_flask = threading.Thread(target=run_flask, daemon=True)
    t_flask.start()

    t_sheet = threading.Thread(target=monitor_new_trades, daemon=True)
    t_sheet.start()

    t_news = threading.Thread(target=fetch_and_post_crypto_news, daemon=True)
    t_news.start()

    bot.infinity_polling()
