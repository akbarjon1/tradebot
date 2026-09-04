import os
import re
import telebot
import google.generativeai as genai
from notion_client import Client
import threading
import time
import feedparser
from flask import Flask, render_template, request
from groq import Groq

# --- 1. SOZLAMALAR VA KALITLAR ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "6722502116:AAGMwQ0EOyYIyGDvpfAB2J9sygrO5yy_DVo")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6IncuV5L-E1RXvISQP2N4XJyGOx27royf8hRUydbg63Ig")
NOTION_API_KEY = "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"
NOTION_DATABASE_ID = "2337d7dfab1a8143a758000bc70b4204"
CHANNEL_CHAT_ID = os.environ.get("CHANNEL_CHAT_ID", "@obsidian_lab_uz")

# AI va Bot obyektlari
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-3.6-flash")

groq_key = os.environ.get("GROQ_API_KEY", "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")
groq_client = Groq(api_key=groq_key) if groq_key else None

notion = Client(auth=NOTION_API_KEY)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)

# --- UNIVERSAL AI FUNKSIYASI ---
def ask_ai(prompt):
    # 1. Gemini bilan urinish
    try:
        res = model.generate_content(prompt)
        if res and res.text:
            return res.text.strip()
    except Exception as gemini_err:
        print(f"⚠️ Gemini ishlamadi: {gemini_err}")

    # 2. Groq (Llama-3) bilan urinish
    if groq_client:
        try:
            print("⚡️ Zaxira: Groq (Llama-3) ishga tushdi...")
            chat_completion = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama3-8b-8192",
            )
            return chat_completion.choices[0].message.content.strip()
        except Exception as groq_err:
            print(f"⚠️ Groq xatolik: {groq_err}")
    else:
        print("⚠️ Groq API kaliti topilmadi!")

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
