import io
import json
import os
import feedparser
import threading
import time
import re
import threading
import time
from datetime import datetime
from PIL import Image
import requests
import telebot
from google import genai
from flask import Flask, render_template, request, jsonify

# ==================== SOZLAMALAR ====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "6722502116:AAGMwQ0EOyYIyGDvpfAB2J9sygrO5yy_DVo")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6IncuV5L-E1RXvISQP2N4XJyGOx27royf8hRUydbg63Ig")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "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")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "2337d7dfab1a801e8ce8f72a83c0389c")

# Agar kanalingiz bo'lsa, ID odatda -100 bilan boshlanadi. Masalan: "-1005436696482"
CHANNEL_CHAT_ID = os.environ.get("CHANNEL_CHAT_ID", "-3814115881")
# ====================================================

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)


def process_with_gemini(user_text, pil_image=None):
    prompt = f"""
    Siz ko'p funksiyali AI yordamchisiz. Foydalanuvchi siz bilan oddiy suhbatlashishi yoki savdo natijasini Notion'ga saqlashni so'rashi mumkin.

    DIQQAT QOIDALAR:
    1. Agar foydalanuvchi SAVDONI Notion'ga saqlashni buyursa:
       "should_save": true qilib, parametrlarni to'ldiring.
    
    2. Agar bu ODDIY SUHBAT yoki SAVOL bo'lsa:
       "should_save": false qiling va "chat_response" qismida do'stona javob bering.

    3. Agar foydalanuvchi RISK yoki LOT hisoblashni so'rasa:
       - "should_save": false qiling.
       - "chat_response" qismida hisob-kitobni aniq lot hajmida bering.

    FAQAT quyidagi JSON formatida javob bering:
    {{
        "should_save": true yoki false,
        "chat_response": "Javob matni",
        "trade_name": "EURUSD Short",
        "pair": "EURUSD",
        "position": "Long yoki Short",
        "risk": 0.5,
        "result": "Open",
        "notes": "Tahlil va izoh"
    }}

    Foydalanuvchi xabari:
    "{user_text if user_text else 'Grafik tahlil qilinsin'}"
    """

    contents = [prompt]
    if pil_image:
        contents.append(pil_image)

    # Server band bo'lsa, avtomatik 3 marta qayta urinish
    for attempt in range(3):
        try:
            response = ai_client.models.generate_content(
                model="gemini-3.6-flash", contents=contents
            )
            clean_json = response.text.strip()
            clean_json = re.sub(
                r"^```json\s*|^```\s*|```$", "", clean_json, flags=re.MULTILINE
            ).strip()
            return json.loads(clean_json)
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            raise e


def save_trade_to_notion(data):
    url = "[https://api.notion.com/v1/pages](https://api.notion.com/v1/pages)"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    today_str = datetime.now().strftime("%Y-%m-%d")

    payload = {
        "parent": {"page_id": NOTION_PARENT_PAGE_ID},
        "properties": {
            "title": [
                {"text": {"content": data.get("trade_name") or "New Trade"}}
            ]
        },
        "children": [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"📊 Savdo tafsilotlari ({today_str})"}}
                    ]
                },
            },
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"Pair: {data.get('pair', 'N/A')}"}}
                    ]
                },
            },
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"Position: {data.get('position', 'N/A')}"}}
                    ]
                },
            },
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"Risk: {data.get('risk', 'N/A')}%"}}
                    ]
                },
            },
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"Result: {data.get('result', 'N/A')}"}}
                    ]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": f"Tahlil va izoh: {data.get('notes', '')}"}}
                    ]
                },
            },
        ],
    }

    res = requests.post(url, headers=headers, json=payload)
    return res.status_code == 200, res.text


def get_trades_from_notion():
    url = f"https://api.notion.com/v1/blocks/{NOTION_PARENT_PAGE_ID}/children?page_size=15"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
    }
    trades = []
    try:
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            blocks = res.json().get("results", [])
            for b in blocks:
                page_id = b.get("id")
                if b.get("type") == "child_page":
                    title = b.get("child_page", {}).get("title", "Savdo Tahlili")
                    created_time = b.get("created_time", "")[:10]

                    child_url = f"https://api.notion.com/v1/blocks/{page_id}/children"
                    c_res = requests.get(child_url, headers=headers)
                    details = []
                    if c_res.status_code == 200:
                        child_blocks = c_res.json().get("results", [])
                        for cb in child_blocks:
                            b_type = cb.get("type")
                            rich_text = cb.get(b_type, {}).get("rich_text", [])
                            if rich_text:
                                details.append(rich_text[0].get("plain_text", ""))

                    full_text = "\n".join(details)
                    trades.append({
                        "id": page_id,
                        "title": title,
                        "date": created_time,
                        "content": full_text if full_text else "Batafsil ma'lumot Notion'da",
                        "raw_blocks": details,
                    })
                elif b.get("type") == "paragraph":
                    text_list = b.get("paragraph", {}).get("rich_text", [])
                    if text_list:
                        trades.append({
                            "id": page_id,
                            "title": "Qayd",
                            "date": b.get("created_time", "")[:10],
                            "content": text_list[0].get("plain_text", ""),
                            "raw_blocks": [],
                        })
    except Exception as e:
        print("Notion o'qishda xatolik:", e)
    return trades


# Telegram komandalari
@bot.message_handler(commands=['calc', 'risk', 'lot'])
def handle_calc_command(message):
    guide_text = (
        "⚖️ **Risk & Lot Kalkulyatori**\n\n"
        "Lot hajmini hisoblash uchun menga quyidagicha yozing:\n"
        "👉 `Depozit 10000$, risk 1%, EURUSD kirish 1.0850, stop 1.0830`"
    )
    bot.reply_to(message, guide_text, parse_mode="Markdown")


@bot.message_handler(commands=["start"])
def send_welcome(message):
    bot.reply_to(
        message,
        "Assalomu alaykum! Men sizning yordamchingizman.\n\n"
        "Men bilan suhbatlashishingiz yoki trading bitimlarini 'Notionga qo'sh' deb yuborishingiz mumkin.",
    )


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    status_msg = bot.reply_to(message, "👀 Rasm ko'rib chiqilmoqda...")
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        pil_image = Image.open(io.BytesIO(downloaded_file))

        user_caption = message.caption or ""
        handle_incoming(message, user_text=user_caption, pil_image=pil_image, existing_status_msg=status_msg)
    except Exception as e:
        bot.edit_message_text(f"❌ Rasmni yuklashda xatolik: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)


@bot.message_handler(func=lambda message: True)
def handle_text_message(message):
    handle_incoming(message, user_text=message.text)


def handle_incoming(message, user_text="", pil_image=None, existing_status_msg=None):
    status_msg = existing_status_msg or bot.reply_to(message, "⏳ O'ylanmoqda...")

    try:
        ai_res = process_with_gemini(user_text, pil_image)

        if not ai_res.get("should_save", False):
            reply_chat = ai_res.get("chat_response", "Sizni tushundim.")
            bot.edit_message_text(reply_chat, chat_id=message.chat.id, message_id=status_msg.message_id)
            return

        success, notion_res = save_trade_to_notion(ai_res)

        if success:
            reply_text = (
                f"✅ **Savdo Notion'ga saqlandi!**\n\n"
                f"📌 **Nom:** {ai_res.get('trade_name')}\n"
                f"📊 **Juftlik:** {ai_res.get('pair')}\n"
                f"🧭 **Yo'nalish:** {ai_res.get('position')}\n"
                f"⚖️ **Risk:** {ai_res.get('risk')}%\n"
                f"🏁 **Natija:** {ai_res.get('result')}\n"
                f"💡 **Tahlil:** {ai_res.get('notes')}"
            )
            bot.edit_message_text(reply_text, chat_id=message.chat.id, message_id=status_msg.message_id, parse_mode="Markdown")
        else:
            bot.edit_message_text(f"❌ Notion xatoligi:\n`{notion_res}`", chat_id=message.chat.id, message_id=status_msg.message_id, parse_mode="Markdown")
    except Exception as e:
        bot.edit_message_text(f"❌ Xatolik yuz berdi: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)


# Flask veb-sayt yo'llari
@app.route('/')
def home():
    trades = get_trades_from_notion()
    return render_template("index.html", trades=trades)


@app.route('/send_feedback', methods=['POST'])
def send_feedback():
    data = request.get_json() or {}
    name = data.get('name', 'Anonim').strip()
    message_text = data.get('message', '').strip()
    
    if not message_text:
        return jsonify({"success": False, "error": "Bo'sh xabar"}), 400

    alert_text = f"💬 <b>Saytdan izoh:</b>\n👤 {name}\n📝 {message_text}"
    try:
        bot.send_message(CHANNEL_CHAT_ID, alert_text, parse_mode="HTML")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# Notion monitoring oqimi (Signallar uchun)
sent_trade_ids = set()

def monitor_new_trades():
    global sent_trade_ids

    # Boshlang'ich bitimlarni ro'yxatga kiritish (eskilarini qayta tashlamasligi uchun)
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
                    # Yangi bitim topildi
                    title = trade.get("title", "Yangi Bitim")
                    content = trade.get("content", "")
                    
                    signal_text = (
                        f"🚨 <b>YANGI SIGNAL / SAVDO!</b>\n\n"
                        f"📌 <b>{title}</b>\n\n"
                        f"{content}\n\n"
                        f"⚡️ <i>Menejment qoidalariga amal qiling!</i>"
                    )
                    
                    try:
                        bot.send_message(CHANNEL_CHAT_ID, signal_text, parse_mode="HTML")
                        sent_trade_ids.add(t_id)
                    except Exception as err:
                        print(f"Kanalga signal yuborishda xatolik: {err}")
        except Exception as e:
            print(f"Monitoring siklida xato: {e}")
            time.sleep(10)


if __name__ == "__main__":
    # 1. Flask veb-serveri
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # 2. Notion monitoringi
    monitor_thread = threading.Thread(target=monitor_new_trades)
    monitor_thread.daemon = True
    monitor_thread.start()

    # 3. Telegram Bot polling
    try:
        bot.remove_webhook()
    except Exception:
        pass

    while True:
        try:
            print("Bot ishga tushdi...")
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"Ulanish xatosi: {e}")
            time.sleep(3)

            # ==========================================
# OBSIDIAN LAB // AVTOMATIK YANGILIKLAR TIZIMI
# ==========================================
SEEN_NEWS = set()

def fetch_and_post_crypto_news():
    """Har 15 daqiqada yangi crypto tahlil va yangiliklarni kanalga uzatadi"""
    RSS_URL = "https://cointelegraph.com/rss"
    
    while True:
        try:
            feed = feedparser.parse(RSS_URL)
            if feed.entries:
                latest = feed.entries[0]
                news_id = latest.get("id", latest.get("link"))
                
                # Agar bu yangilik hali kanalga chiqmagan bo'lsa
                if news_id not in SEEN_NEWS:
                    SEEN_NEWS.add(news_id)
                    title = latest.title
                    summary = latest.get("summary", "")[:300]
                    
                    # Gemini orqali qisqa tahliliy xulosa tayyorlash
                    prompt = f"""
                    Quyidagi kripto yangilikni o'zbek tilida, Obsidian Lab kiber-tahlil uslubida qisqacha (maksimum 2-3 jumla) qilib yozib ber:
                    Sarlavha: {title}
                    Tafsilot: {summary}
                    
                    Format quyidagicha bo'lsin:
                    ⚡️ // OBSIDIAN RADAR: [Qisqa o'zbekcha sarlavha]
                    
                    [2 ta jumlada asosiy mazmun va bozorga ta'siri]
                    
                    🔗 Manba: CoinTelegraph
                    """
                    
                    try:
                        ai_response = model.generate_content(prompt)
                        post_text = ai_response.text
                    except Exception:
                        post_text = f"⚡️ // OBSIDIAN RADAR\n\n{title}\n\n🔗 {latest.link}"
                    
                    # Kanalga xabarni yuborish
                    if CHANNEL_CHAT_ID:
                        bot.send_message(
                            chat_id=CHANNEL_CHAT_ID,
                            text=post_text,
                            disable_web_page_preview=False
                        )
        except Exception as e:
            print(f"Yangiliklar tizimida xatolik: {e}")
            
        # 15 daqiqa (900 soniya) kutish
        time.sleep(3600)

# Yangiliklar skanerini fon rejimida ishga tushirish
news_thread = threading.Thread(target=fetch_and_post_crypto_news, daemon=True)
news_thread.start()
