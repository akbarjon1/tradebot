import io
import json
import re
from datetime import datetime
from PIL import Image
import requests
import telebot
from google import genai
from flask import Flask, render_template

# ==================== SOZLAMALAR ====================
TELEGRAM_BOT_TOKEN = "6722502116:AAGk-_T7sDQvA1W3X50ADbJZhQLIIC65sLA"
GEMINI_API_KEY = "AQ.Ab8RN6IncuV5L-E1RXvISQP2N4XJyGOx27royf8hRUydbg63Ig"
NOTION_TOKEN = "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"
NOTION_PARENT_PAGE_ID = "2337d7dfab1a801e8ce8f72a83c0389c"
# ====================================================

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)


def process_with_gemini(user_text, pil_image=None):
    prompt = f"""
    Siz ko'p funksiyali AI yordamchisiz. Foydalanuvchi siz bilan oddiy suhbatlashishi (savol berishi, ob-havo so'rashi, maslahat olishi) yoki savdo (trading) natijasini Notion'ga saqlashni so'rashi mumkin.

    DIQQAT QOIDALAR:
    1. Agar foydalanuvchi SAVDONI Notion'ga saqlashni buyursa (masalan, trading signallari, tahlil berib 'Notionga qo'sh', 'saqla', 'yozib qo'y' desa yoki aniq trade yozsa):
       "should_save": true qilib, parametrlarni to'ldiring.
    
    2. Agar bu ODDIY SUHBAT yoki SAVOL bo'lsa (masalan: "havo qanaqa", "salom", "qalesan", "trading nima?"):
       "should_save": false qiling va "chat_response" qismida foydalanuvchiga do'stona, aniq javob bering. Hech qanday "xatolik" demang. (Masalan, ob-havo so'rasa, joylashuvga qarab umumiy iliq javob bering).

    3. Agar foydalanuvchi RISK yoki LOT hisoblashni so'rasa (masalan: "EURUSD depozit 5000, risk 1%, kirish 1.0850, stop 1.0830 lot hisobla"):
   - "should_save": false qiling.
   - "chat_response" qismida:
     * Depozit va risk summasini ($ da)
     * Stop-loss masofasini (pip yoki punktda)
     * Tavsiya qilingan aniq LOT hajmini (lot formulasiga ko'ra)
     * Qisqa va lo'nda professional formatda hisoblab bering.

    FAQAT quyidagi JSON formatida javob bering:
    {{
        "should_save": true yoki false,
        "chat_response": "Agar should_save false bo'lsa, foydalanuvchiga beriladigan to'liq javobingiz",
        "trade_name": "EURUSD Short (agar should_save true bo'lsa)",
        "pair": "Valyuta juftligi (masalan: EURUSD, XAUUSD)",
        "position": "Long yoki Short",
        "risk": 0.5,
        "result": "Profit, Loss, BE yoki Open",
        "notes": "Tahlil va izoh"
    }}

    Foydalanuvchi xabari:
    "{user_text if user_text else 'Grafik tahlil qilinsin'}"
    """

    contents = [prompt]
    if pil_image:
        contents.append(pil_image)

    response = ai_client.models.generate_content(
        model="gemini-3.6-flash", contents=contents
    )

    clean_json = response.text.strip()
    clean_json = re.sub(
        r"^```json\s*|^```\s*|```$", "", clean_json, flags=re.MULTILINE
    ).strip()

    return json.loads(clean_json)


def save_trade_to_notion(data):
    url = "https://api.notion.com/v1/pages"
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
                        {
                            "type": "text",
                            "text": {
                                "content": f"📊 Savdo tafsilotlari ({today_str})"
                            },
                        }
                    ]
                },
            },
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"Pair: {data.get('pair', 'N/A')}"
                            },
                        }
                    ]
                },
            },
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"Position: {data.get('position', 'N/A')}"
                            },
                        }
                    ]
                },
            },
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"Risk: {data.get('risk', 'N/A')}%"
                            },
                        }
                    ]
                },
            },
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"Result: {data.get('result', 'N/A')}"
                            },
                        }
                    ]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"Tahlil va izoh: {data.get('notes', '')}"
                            },
                        }
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
                # Agar child_page bo'lsa
                if b.get("type") == "child_page":
                    page_id = b.get("id")
                    title = b.get("child_page", {}).get("title", "Savdo Tahlili")
                    created_time = b.get("created_time", "")[:10]  # Sana: YYYY-MM-DD
                    
                    # Sahifa ichidagi bloklarni o'qish
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
                    
                    # Matnlarni birlashtirib, parametrlarni ajratib olish
                    full_text = "\n".join(details)
                    trades.append({
                        "title": title,
                        "date": created_time,
                        "content": full_text if full_text else "Batafsil ma'lumot Notion'da",
                        "raw_blocks": details
                    })
                elif b.get("type") == "paragraph":
                    text_list = b.get("paragraph", {}).get("rich_text", [])
                    if text_list:
                        trades.append({
                            "title": "Qayd",
                            "date": b.get("created_time", "")[:10],
                            "content": text_list[0].get("plain_text", ""),
                            "raw_blocks": []
                        })
    except Exception as e:
        print("Notion o'qishda xatolik:", e)
    return trades

@bot.message_handler(commands=['calc', 'risk', 'lot'])
def handle_calc_command(message):
    guide_text = (
        "⚖️ **Risk & Lot Kalkulyatori**\n\n"
        "Lot hajmini hisoblash uchun menga quyidagicha yozing:\n"
        "👉 `Depozit 10000$, risk 1%, EURUSD kirish 1.0850, stop 1.0830`\n"
        "👉 `XAUUSD (Oltin) 2000$ balans, 2% risk, kirish 2500, stop 2490`\n\n"
        "Men sizga ochishingiz kerak bo'lgan aniq **Lot hajmi**ni hisoblab beraman!"
    )
    bot.reply_to(message, guide_text, parse_mode="Markdown")

@bot.message_handler(commands=["start"])
def send_welcome(message):
    bot.reply_to(
        message,
        "Assalomu alaykum! Men sizning yordamchingizman.\n\n"
        "Men bilan bemalol suhbatlashishingiz yoki trading savdolaringizni 'Notionga qo'sh' deb yuborishingiz mumkin.",
    )

@bot.message_handler(commands=['radar', 'brief'])
def send_morning_radar(message):
    bot.send_chat_action(message.chat.id, 'typing')
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""
    Sen professional treyding yordamchisisan. Bugungi sana: {today}.
    Foydalanuvchi uchun professional, qisqa va lo'nda "Ertalabki Bozor Radari"ni tayyorlab ber.
    
    Format:
    🌅 BOZOR RADARI | {today}
    
    📊 Bugungi Sessiyalar Rejasi:
    - London sessiyasi: (Kutilmalar)
    - Nyu-York sessiyasi: (Kutilmalar)
    
    ⚠️ Yangiliklar va Xavflar:
    - Diqqat qilinishi kerak bo'lgan soatlar.
    
    🧠 Kunlik Mindset:
    - Qisqa intizom eslatmasi.
    """
    try:
        response = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        bot.reply_to(message, response.text)
    except Exception as e:
        bot.reply_to(message, f"Radar xatosi: {e}")

@bot.message_handler(
    content_types=["text"],
    func=lambda msg: not msg.text.startswith("/"),
)
def handle_text(message):
    handle_incoming(message, user_text=message.text, pil_image=None)


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    status_msg = bot.reply_to(message, "👀 Rasm ko'rib chiqilmoqda...")

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        pil_image = Image.open(io.BytesIO(downloaded_file))

        user_caption = message.caption or ""
        handle_incoming(
            message,
            user_text=user_caption,
            pil_image=pil_image,
            existing_status_msg=status_msg,
        )
    except Exception as e:
        bot.edit_message_text(
            f"❌ Rasmni yuklashda xatolik: {str(e)}",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
        )


def handle_incoming(
    message, user_text="", pil_image=None, existing_status_msg=None
):
    if existing_status_msg:
        status_msg = existing_status_msg
    else:
        status_msg = bot.reply_to(message, "⏳ O'ylanmoqda...")

    try:
        ai_res = process_with_gemini(user_text, pil_image)

        # 1. Agar foydalanuvchi shunchaki gaplashgan bo'lsa
        if not ai_res.get("should_save", False):
            reply_chat = ai_res.get(
                "chat_response", "Sizni tushundim, yana qanday yordam bera olaman?"
            )
            bot.edit_message_text(
                reply_chat,
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
            )
            return

        # 2. Agar Notion'ga qo'shish so'ralgan bo'lsa
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
            bot.edit_message_text(
                reply_text,
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                parse_mode="Markdown",
            )
        else:
            bot.edit_message_text(
                f"❌ Notion xatoligi:\n`{notion_res}`",
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                parse_mode="Markdown",
            )
    except Exception as e:
        bot.edit_message_text(
            f"❌ Xatolik yuz berdi: {str(e)}",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
        )


import os
import threading

app = Flask(__name__)

@app.route("/")
def home():
    trades = get_trades_from_notion()
    return render_template("index.html", trades=trades)

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

import time

try:
    bot.remove_webhook()
except Exception:
    pass

while True:
    try:
        print("Bot ishga tushdi...")
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        print(f"Ulanish xatosi (qayta urinish): {e}")
        time.sleep(3)
