import os
import telebot
import google.generativeai as genai
from notion_client import Client
from flask import Flask, render_template
import threading
import time
import feedparser

# --- 1. SOZLAMALAR VA KALITLAR ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "6722502116:AAGMwQ0EOyYIyGDvpfAB2J9sygrO5yy_DVo")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
CHANNEL_CHAT_ID = os.environ.get("CHANNEL_CHAT_ID", "@obsidian_lab_uz")

# AI va Telegram obyektlari
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")
notion = Client(auth=NOTION_API_KEY)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)

# --- 2. NOTION FUNKSIYALARI ---
def get_trades_from_notion():
    try:
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
    try:
        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Name": {"title": [{"text": {"content": title}}]},
                "Tahlil": {"rich_text": [{"text": {"content": content}}]}
            }
        )
        return True
    except Exception as e:
        print(f"Notionga yozishda xatolik: {e}")
        return False

# --- 3. FLASK WEB SAYTI ---
@app.route('/')
def home():
    trades = get_trades_from_notion()
    return render_template('index.html', trades=trades)

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- 4. TELEGRAM BOT HANDLERLAR ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Obsidian Lab Terminaliga xush kelibsiz! Savdo signallarini yuboring.")

@bot.message_handler(func=lambda message: True)
def handle_trade_message(message):
    user_text = message.text
    prompt = f"""
    Quyidagi savdo signalini tahlil qil va Notion uchun qisqa sarlavha va asosiy parametrlarni ajratib ber:
    {user_text}
    """
    try:
        ai_res = model.generate_content(prompt)
        title = user_text[:30]
        content = ai_res.text
        save_trade_to_notion(title, content)
        bot.reply_to(message, f"Bitim Notion bazasiga saqlandi!\n\nAI Xulosasi:\n{content}")
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
    RSS_URLS = [
        "https://cointelegraph.com/rss",
        "https://feeds.feedburner.com/CoinDesk"
    ]
    while True:
        try:
            print("LOG: [Obsidian Radar] Yangiliklar tekshirilmoqda...")
            for url in RSS_URLS:
                feed = feedparser.parse(url)
                if feed.entries:
                    latest = feed.entries[0]
                    news_id = latest.get("id", latest.get("link"))
                    if news_id not in SEEN_NEWS:
                        SEEN_NEWS.add(news_id)
                        title = latest.title
                        summary = latest.get("summary", "")[:300]
                        link = latest.get("link", "")
                        
                        prompt = f"""
                        Sen "Obsidian Lab" kiber-tahlil laboratoriyasining yetakchi kripto tahlilchisisan.
                        Quyidagi yangilikni o'zbek tiliga tarjima qilib, chuqur tahliliy va lo'nda ko'rinishda yozib ber.

                        Sarlavha: {title}
                        Tafsilot: {summary}

                        Talablar:
                        - Sarlavhani o'zbek tiliga jiddiy, professional qilib o'gir.
                        - Voqea mazmunini 2 jumlada tushuntir.
                        - Bozorga yoki treyderlarga ta'sirini 1 jumlada tahlil qil.
                        - HTML teglaridan (<tg-spoiler>, <b>, <i>) foydalan, markdown yozma.

                        Format aynan shunday bo'lsin:
                        ⚡️ <b>// OBSIDIAN RADAR</b>

                        📌 <b>[O'zbekcha Sarlavha]</b>

                        📖 <b>Tafsilot:</b> [Mazmuni]

                        💡 <b>Tahlil:</b> [Bozorga ta'siri]

                        🔗 <a href="{link}">To'liq o'qish</a>
                        """
                        try:
                            ai_response = model.generate_content(prompt)
                            post_text = ai_response.text
                        except Exception as ai_err:
                            print(f"AI Xatolik: {ai_err}")
                            post_text = f"⚡️ <b>// OBSIDIAN RADAR:</b> {title}\n\n🔗 <a href='{link}'>Batafsil</a>"
                        
                        bot.send_message(
                            chat_id=CHANNEL_CHAT_ID,
                            text=post_text,
                            parse_mode="HTML",
                            disable_web_page_preview=False
                        )
                        print(f"LOG: [Obsidian Radar] Kanalga chiroyli post chiqdi: {title}")
                        break
        except Exception as e:
            print(f"LOG: Yangiliklar tizimida xatolik: {e}")
            
        time.sleep(3600)

# --- 7. ISHGA TUSHIRISH ---
if __name__ == "__main__":
    t_flask = threading.Thread(target=run_flask, daemon=True)
    t_flask.start()

    t_notion = threading.Thread(target=monitor_new_trades, daemon=True)
    t_notion.start()

    t_news = threading.Thread(target=fetch_and_post_crypto_news, daemon=True)
    t_news.start()

    bot.infinity_polling()
