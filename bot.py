import os
import re
import time
import threading
import feedparser
import telebot
from flask import Flask, render_template, request
from google import genai
from google.genai import types as genai_types
from groq import Groq
from notion_client import Client

# --- 1. SOZLAMALAR VA KALITLAR ---
def get_env(key, default=""):
    val = os.environ.get(key, default)
    return val.strip() if val else default

TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", "6722502116:AAGMwQ0EOyYIyGDvpfAB2J9sygrO5yy_DVo")
GEMINI_API_KEY = get_env("GEMINI_API_KEY")
GROQ_API_KEY = get_env("GROQ_API_KEY")
NOTION_API_KEY = get_env("NOTION_API_KEY")
NOTION_DATABASE_ID = get_env("NOTION_DATABASE_ID")
CHANNEL_CHAT_ID = get_env("CHANNEL_CHAT_ID", "@obsidian_lab_uz")

# AI Mijozlari
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Notion mijozi (InvalidRequestURL xatosini to'liq yechish)
notion = Client(auth=NOTION_API_KEY) if NOTION_API_KEY else None
NOTION_DATA_SOURCE_ID = None

if notion and NOTION_DATABASE_ID:
    try:
        db = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)
        if "data_sources" in db and len(db["data_sources"]) > 0:
            NOTION_DATA_SOURCE_ID = db["data_sources"][0]["id"]
            print(f"✅ Notion Data Source ulandi: {NOTION_DATA_SOURCE_ID}")
    except Exception as e:
        print(f"⚠️ Notion bazasini aniqlashda ogohlantirish: {e}")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)

# --- 2. UNIVERSAL AI TAHLIL FUNKSIYASI ---
def get_ai_analysis(prompt: str) -> str:
    # 1. Yangi SDK bilan Gemini
    if gemini_client:
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=800,
                ),
            )
            text = (response.text or "").strip()
            if text:
                return text
        except Exception as gemini_err:
            print(f"⚠️ Gemini ishlamadi: {gemini_err}")

    # 2. Zaxira: Groq (Amaldagi openai/gpt-oss-120b modeli)
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

# --- 3. NOTION FUNKSIYALARI ---
def get_trades_from_notion():
    if not notion:
        return []
    try:
        # Yangi data_sources yoki eski databases query moslashuvi
        if NOTION_DATA_SOURCE_ID and hasattr(notion, 'data_sources'):
            response = notion.data_sources.query(data_source_id=NOTION_DATA_SOURCE_ID)
        else:
            response = notion.databases.query(database_id=NOTION_DATABASE_ID)

        trades = []
        for row in response.get("results", []):
            trade_id = row.get("id")
            props = row.get("properties", {})
            title_prop = props.get("Name", {}).get("title", [])
            title = title_prop[0].get("plain_text", "Nomsiz") if title_prop else "Nomsiz"
            content_prop = props.get("Tahlil", {}).get("rich_text", [])
            content = content_prop[0].get("plain_text", "") if content_prop else ""
            date_prop = row.get("created_time", "")[:10]
            trades.append({
                "id": trade_id,
                "title": title,
                "content": content,
                "date": date_prop
            })
        return trades
    except Exception as e:
        print(f"Notion xatolik: {e}")
        return []

def save_trade_to_notion(title, content):
    if not notion or not NOTION_DATABASE_ID:
        return False, "Notion sozlamalari to'liq emas"
    try:
        safe_content = content[:2000]
        safe_title = title[:100]
        
        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Name": {"title": [{"text": {"content": safe_title}}]},
                "Tahlil": {"rich_text": [{"text": {"content": safe_content}}]}
            }
        )
        return True, "Muvaffaqiyatli saqlandi"
    except Exception as e:
        err_msg = str(e)
        print(f"Notionga yozishda xatolik: {err_msg}")
        return False, err_msg

# --- 4. FLASK WEB SAYTI ---
CHANNEL_ID = "-5436696482"
comments_store = []

@app.route('/')
def home():
    trades = get_trades_from_notion()
    return render_template('index.html', trades=trades, comments=comments_store)

@app.route('/add_comment', methods=['POST'])
def add_comment():
    import datetime
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
    bot.reply_to(message, "Salom bratva! Obsidian Radar yonizda. Bozor qon yig'layaptimi yo yashil shamlar bormi? Xullas, signal bo'lsa tashlang bazaga tiqamiz, savol bo'lsa bemalol — gaplashamiz!")

@bot.message_handler(func=lambda message: True)
def handle_trade_message(message):
    user_text = message.text
    
    # Kripto-slang va erkin stil uchun prompt
    prompt = f"""Sen Obsidian Lab kanalining ashaddiy kripto treyder AI yordamchisisan.
Xaraktering: O'zbekcha kripto-slanglarda gapirasan ("brat", "jigar", "kotletit qildik", "rek bo'ldik", "fomo", "to the moon", "qizil sham", "likvidatsiya bo'lma", "raketa", "dipdan ilish"). Hech qanaqa rasmiyatchilik yo'q, xuddi choyxonada kripto muhokama qilayotgan tajribali oshnadeksan. Hazil-mutoyiba va qochirimlar bo'lsin.

Foydalanuvchi yozdi: "{user_text}"

Qoidalar:
1. Agar foydalanuvchi shunchaki gaplashsa ("nima gap", "qalesan", "bozor nima bo'lyapti" va h.k.):
   - Unga toza treydercha slanglar bilan, qiziqarli, kulgili va jonli javob qaytar. Qisqa va lo'nda bo'lsin.
2. Agar bu aniq savdo signali bo'lsa (Entry, TP, SL, Long/Short kabi aniq raqamlar bo'lsa):
   - Javobning eng birinchi so'zi aniq "SIGNAL_DETECTED" bo'lsin.
   - Keyingi qatordan signalni qisqa, tushunarli formatda tahlil qilib ber (masalan: "Riskni boshqar, stopni unutma").
"""
    try:
        content = get_ai_analysis(prompt)
        if not content:
            bot.reply_to(message, "Ey jigar, tarmoqda tiqilinch bo'p qoldi, birozdan keyin yozvor.")
            return

        # Agar savdo signali bo'lsa
        if content.startswith("SIGNAL_DETECTED"):
            clean_content = content.replace("SIGNAL_DETECTED", "").strip()
            title = user_text[:30]
            success, msg = save_trade_to_notion(title, clean_content)
            
            reply_text = f"🎯 *Signal Notion'ga qadab qo'yildi, brat!*\n\n{clean_content}\n\n⚠️ _Kotletit qilib yuborma, risk-menejment esdan chiqmasin!_"
            bot.reply_to(message, reply_text, parse_mode="Markdown")
        else:
            # Oddiy suhbat
            bot.reply_to(message, content)

    except Exception as e:
        bot.reply_to(message, f"Brat, xatolik berdi: {e}")

# --- 6. NOTION MONITORING ---
sent_trade_ids = set()

def monitor_new_trades():
    global sent_trade_ids
    try:
        initial = get_trades_from_notion()
        for t in initial:
            if t.get("id"):
                sent_trade_ids.add(t["id"])
    except Exception as e:
        print(f"Monitoring boshlanishida ogohlantirish: {e}")

    while True:
        try:
            time.sleep(60)
            trades = get_trades_from_notion()
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

    t_notion = threading.Thread(target=monitor_new_trades, daemon=True)
    t_notion.start()

    t_news = threading.Thread(target=fetch_and_post_crypto_news, daemon=True)
    t_news.start()

    bot.infinity_polling()
