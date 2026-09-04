import os
import re
import telebot
import google.generativeai as genai
from notion_client import Client
from flask import Flask, render_template
import threading
import time
import feedparser

# --- 1. SOZLAMALAR VA KALITLAR ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "6722502116:AAGMwQ0EOyYIyGDvpfAB2J9sygrO5yy_DVo")

# O'zingizning Google Gemini kalitingizni mana shu qo'shtirnoq ichiga yozing:
GEMINI_API_KEY = "AQ.Ab8RN6IncuV5L-E1RXvISQP2N4XJyGOx27royf8hRUydbg63Ig" 

NOTION_API_KEY = os.environ.get("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")
NOTION_DATABASE_ID = os.environ.get("2927d7dfab1a8000953ef1a2c403ecb2")
CHANNEL_CHAT_ID = os.environ.get("CHANNEL_CHAT_ID", "@obsidian_lab_uz")

# AI va Bot obyektlari
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-3.6-flash")
notion = Client(auth=NOTION_API_KEY)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)

# --- 2. NOTION FUNKSIYALARI ---
def get_trades_from_notion():
    try:
        # Yangi va eski notion-client versiyalariga mos query
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
        # Notion bitta blokda 2000 belgidan oshig'ini qabul qilmaydi
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
        
        success, msg = save_trade_to_notion(title, content)
        if success:
            bot.reply_to(message, f"Bitim Notion bazasiga saqlandi!\n\nAI Xulosasi:\n{content}")
        else:
            bot.reply_to(message, f"AI tahlili tayyor, lekin Notion'ga saqlashda xatolik bo'ldi: {msg}\n\nAI Xulosasi:\n{content}")
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
                        raw_summary = latest.get("summary", "")[:400]
                        link = latest.get("link", "")
                        
                        # HTML teglarni matndan tozalaymiz:
                        clean_summary = re.sub('<[^<]+?>', '', raw_summary).strip()
                        
                        prompt = f"""
Sen Obsidian Lab tahliliy kripto kanali uchun post yozuvchi AI bo'lasan.
Quyidagi yangilikni o'zbek tiliga tarjima qilib, treyderlar uchun tushunarli va professional ko'rinishda ber:

Sarlavha: {title}
Mazmuni: {clean_summary}

Format aynan mana shunday bo'lsin:
⚡️ // OBSIDIAN RADAR: [O'zbekcha qisqa sarlavha]

📌 Tafsilot: [Voqea haqida 2 jumlada asosiy mazmun]

💡 Tahlil: [Bozorga yoki treyderlarga ta'siri haqida 1 jumla]

🔗 Manba: {link}
"""
                        try:
                            ai_response = model.generate_content(prompt)
                            post_text = ai_response.text.strip()
                        except Exception as ai_err:
                            print(f"AI Xatolik sababi: {ai_err}")
                            post_text = f"⚡️ // OBSIDIAN RADAR: {title}\n\n📌 Tafsilot: {clean_summary[:200]}...\n\n🔗 Manba: {link}"
                        
                        bot.send_message(
                            chat_id=CHANNEL_CHAT_ID,
                            text=post_text,
                            disable_web_page_preview=False
                        )
                        print("LOG: [Obsidian Radar] Kanalga post chiqdi!")
                        break
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
