import os
import re
import telebot
import threading
import time
import feedparser
from flask import Flask, render_template, request

# --- YANGI SDK'LAR (2026-yil holatiga mos) ---
# DIQQAT: "google.generativeai" kutubxonasi 2025-yil 30-noyabrda butunlay
# to'xtatilgan (EOL). O'rniga rasmiy, birlashtirilgan "google-genai" ishlatiladi.
#   pip uninstall google-generativeai
#   pip install google-genai
from google import genai
from google.genai import types as genai_types

from groq import Groq
from notion_client import Client


# --- 1. SOZLAMALAR VA KALITLAR ---
# Kalitlarni kodga hardcode QILMANG. Ularni Render.com'ning
# Dashboard -> Environment bo'limida saqlang va shu yerdan o'qing.
# (Hardcode qilingan zaxira qiymatlar xavfsizlik uchun olib tashlandi —
#  pastda .strip() bilan probel/qator ko'chirish xatolarining oldi olinadi.)
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"].strip()
GROQ_API_KEY = os.environ["GROQ_API_KEY"].strip()
NOTION_API_KEY = os.environ["NOTION_API_KEY"].strip()
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"].strip()
CHANNEL_CHAT_ID = os.environ.get("CHANNEL_CHAT_ID", "@obsidian_lab_uz")


# --- 2. AI MIJOZLARI ---

# Gemini: yangi Client-asosidagi sintaksis
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Model tanlovi (barchasi hozir GA holatida, google.ai.dev/gemini-api/docs/changelog):
#   "gemini-2.5-flash"  -> eng barqaror, uzoq muddatli, arzon
#   "gemini-3.6-flash"  -> yangiroq avlod, kod/agentic vazifalar uchun kuchliroq
GEMINI_MODEL = "gemini-2.5-flash"

# Groq: llama-3.3-70b-versatile va llama-3.1-8b-instant 2026-08-16'da
# butunlay o'chirilgan. Hozirgi tavsiya etilgan modellar:
groq_client = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL = "openai/gpt-oss-120b"  # asosiy zaxira model


# --- 3. NOTION MIJOZI ---
notion = Client(auth=NOTION_API_KEY)  # standart Notion-Version: 2025-09-03


def get_data_source_id(database_id: str) -> str:
    """
    2025-yil sentabrdan Notion bazalar "data source" orqali so'raladi.
    Eski uslub (to'g'ridan-to'g'ri database_id bilan .query()) ba'zan
    "InvalidRequestURL" xatosini beradi. Shu funksiya bazaga tegishli
    data_source_id'ni bir marta olib beradi.
    """
    db = notion.databases.retrieve(database_id=database_id)
    return db["data_sources"][0]["id"]


try:
    NOTION_DATA_SOURCE_ID = get_data_source_id(NOTION_DATABASE_ID)
except Exception as e:
    print(f"[Notion] data_source_id olishda xato: {e}")
    NOTION_DATA_SOURCE_ID = None


def notion_query(**kwargs):
    """
    Eski notion.databases.query(database_id=...) o'rniga shu funksiyani
    ishlating. Masalan:
        notion_query(filter={...}, sorts=[...])
    """
    if not NOTION_DATA_SOURCE_ID:
        raise RuntimeError("NOTION_DATA_SOURCE_ID aniqlanmagan — Notion ulanishini tekshiring.")
    return notion.data_sources.query(data_source_id=NOTION_DATA_SOURCE_ID, **kwargs)


# --- 4. AI TAHLIL FUNKSIYASI: avval Gemini, xato bo'lsa — darhol Groq ---
def get_ai_analysis(prompt: str) -> str:
    """
    1) Gemini orqali javob olishga harakat qiladi.
    2) Har qanday xato bo'lsa (limit, tarmoq, kalit, model va h.k.) —
       xatoni log qilib, darhol Groq (fallback)ga o'tadi.
    3) Ikkalasi ham ishlamasa — foydalanuvchiga tushunarli xabar qaytaradi.
    """
    # 1-urinish: Gemini
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=1024,
            ),
        )
        text = (response.text or "").strip()
        if text:
            return text
        raise ValueError("Gemini bo'sh javob qaytardi")
    except Exception as gemini_error:
        print(f"[Gemini xato] {gemini_error}")

    # 2-urinish: Groq (zaxira)
    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024,
        )
        text = (completion.choices[0].message.content or "").strip()
        if text:
            return text
        raise ValueError("Groq bo'sh javob qaytardi")
    except Exception as groq_error:
        print(f"[Groq xato] {groq_error}")

    # Ikkalasi ham ishlamadi
    return "⚠️ AI xizmatlarida vaqtinchalik uzilish yuz berdi. Birozdan so'ng qayta urinib ko'ring."


# --- 5. BOT VA FLASK OBYEKTLARI ---
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)

# --- UNIVERSAL AI FUNKSIYASI ---
def ask_ai(prompt):
    err_log = []
    
    # 1. Gemini bilan urinish
    try:
        res = model.generate_content(prompt)
        if res and res.text:
            return res.text.strip()
    except Exception as gemini_err:
        err_msg = f"Gemini: {str(gemini_err)[:80]}"
        print(f"⚠️ {err_msg}")
        err_log.append(err_msg)

    # 2. Groq (Llama-3) bilan urinish
    if groq_client:
        try:
            print("⚡️ Zaxira: Groq ishga tushdi...")
            chat_completion = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile",
            )
            return chat_completion.choices[0].message.content.strip()
        except Exception as groq_err:
            err_msg = f"Groq: {str(groq_err)[:80]}"
            print(f"⚠️ {err_msg}")
            err_log.append(err_msg)
    else:
        err_log.append("Groq kaliti Render Environment'da topilmadi!")

    print(f"Barcha AI xatolari: {err_log}")
    return None

# --- 2. NOTION FUNKSIYALARI ---
def get_trades_from_notion():
    try:
        if hasattr(notion.databases, 'query'):
            response = notion.databases.query(database_id=NOTION_DATABASE_ID)
        else:
            response = notion.request(path=f"databases/{NOTION_DATABASE_ID}/query", method="POST")
            
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
    try:
        safe_content = content[:2000]
        safe_title = title[:100]
        
        response = notion.pages.create(
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

# --- 3. FLASK WEB SAYTI ---
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

# --- 4. TELEGRAM BOT HANDLERLAR ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Salom! Men Obsidian Radar botiman. Menga istalgan savdo signali matnini yuborsangiz, uni tahlil qilib Notion bazasiga saqlayman.")

@bot.message_handler(func=lambda message: True)
def handle_trade_message(message):
    user_text = message.text
    prompt = f"Quyidagi savdo signalini tahlil qil va Notion uchun qisqa sarlavha va asosiy parametrlarni ajratib ber:\n{user_text}"
    
    try:
        content = ask_ai(prompt)
        if not content:
            bot.reply_to(message, "⚠️ AI xizmatlarida vaqtinchalik uzilish yuz berdi.")
            return

        title = user_text[:30]
        success, msg = save_trade_to_notion(title, content)
        if success:
            bot.reply_to(message, f"Bitim Notion bazasiga saqlandi!\n\nAI Xulosasi:\n{content}")
        else:
            bot.reply_to(message, f"AI tahlili tayyor, lekin Notion'ga saqlashda muammo bo'ldi: {msg}\n\nAI Xulosasi:\n{content}")
    except Exception as e:
        bot.reply_to(message, f"Xatolik yuz berdi: {e}")

# --- 5. NOTION MONITORING ---
sent_trade_ids = set()

def monitor_new_trades():
    global sent_trade_ids
    try:
        initial = get_trades_from_notion()
        for t in initial:
            if t.get("id"):
                sent_trade_ids.add(t["id"])
    except Exception as e:
        print(f"Monitoring boshlanishida xatolik: {e}")

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

# --- 6. AVTOMATIK YANGILIKLAR TIZIMI ---
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
                    post_text = ask_ai(prompt)

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

# --- 7. TIZIMNI ISHGA TUSHIRISH ---
if __name__ == "__main__":
    t_flask = threading.Thread(target=run_flask, daemon=True)
    t_flask.start()

    t_notion = threading.Thread(target=monitor_new_trades, daemon=True)
    t_notion.start()

    t_news = threading.Thread(target=fetch_and_post_crypto_news, daemon=True)
    t_news.start()

    bot.infinity_polling()
