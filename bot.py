import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import io
import json
import re
from datetime import datetime
from PIL import Image
import requests
import telebot
from google import genai

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
        model="gemini-1.5-flash", contents=contents
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


@bot.message_handler(commands=["start"])
def send_welcome(message):
    bot.reply_to(
        message,
        "Assalomu alaykum! Men sizning yordamchingizman.\n\n"
        "Men bilan bemalol suhbatlashishingiz yoki trading savdolaringizni 'Notionga qo'sh' deb yuborishingiz mumkin.",
    )


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


print("Bot ishga tushdi...")
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive")

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()
bot.infinity_polling()
