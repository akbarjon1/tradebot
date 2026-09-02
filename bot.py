import os
import time
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
Siz ko'p funksiyali AI yordamchisiz. Foydalanuvchi siz bilan oddiy suhbatlashishi (savol berishi, ob-havo so'rashi, maslahat olishi) yoki savdo grafiki (trading chart) tahlilini so'rashi mumkin.

QAT'IY QOIDA: Javobingiz FAQAT va FAQAT to'g'ri JSON formatida bo'lishi shart. JSON'dan tashqari hech qanday so'z, tushuntirish yoki belgi yozmang.

1-HOLAT: Foydalanuvchi shunchaki gaplashsa yoki grafik bo'lmagan savol bersa, faqat quyidagi JSON formatida javob bering:
{{
  "type": "chat",
  "reply": "Foydalanuvchiga to'liq, samimiy va chiroyli o'zbek tilidagi javobingiz"
}}

2-HOLAT: Foydalanuvchi grafik yuborsa yoki savdo (treyding) tahlilini so'rasa, quyidagi JSON formatida to'liq tahlil bering:
{{
  "type": "trade",
  "data": {{
    "pair": "Valyuta juftligi (masalan: EUR/USD)",
    "timeframe": "Taymfreym (masalan: 1H)",
    "position": "Long yoki Short",
    "risk": 0.5,
    "result": "Profit, Loss, BE yoki Open",
    "notes": "Tahlil va izoh"
  }}
}}

Foydalanuvchi xabari:
"{user_text if user_text else 'Grafik tahlil qilinsin'}"
"""

    contents = [prompt]
    if pil_image:
        contents.append(pil_image)

    response = None
    for attempt in range(3):
        try:
            response = ai_client.models.generate_content(
                model="gemini-3.6-flash",
                contents=contents
            )
            break
        except Exception as err:
            if "503" in str(err) and attempt < 2:
                time.sleep(2)
                continue
            raise err

    clean_json = response.text.strip()
    clean_json = re.sub(
        r"^```json\s*|^```\s*|```$", "", clean_json, flags=re.MULTILINE
    ).strip()

    return json.loads(clean_json)


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
                {
                    "text": {
                        "content": f"{data.get('pair', 'Trade')} - {today_str}"
                    }
                }
            ]
        },
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"Juftlik: {data.get('pair')}\nTaymfreym: {data.get('timeframe')}\nPozitsiya: {data.get('position')}\nRisk: {data.get('risk')}%\nNatija: {data.get('result')}\nIzoh: {data.get('notes')}"
                            },
                        }
                    ]
                },
            }
        ],
    }

    res = requests.post(url, json=payload, headers=headers)
    return res.status_code == 200


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(
        message,
        "Assalomu alaykum! Men bilan xohlagan mavzuda suhbatlashishingiz yoki savdo grafigini tahlil qilish uchun rasm yuborishingiz mumkin.",
    )


@bot.message_handler(content_types=["text"])
def handle_text(message):
    status_msg = bot.reply_to(message, "Javob tayyorlanmoqda... ⏳")
    try:
        result = process_with_gemini(user_text=message.text)
        if result.get("type") == "trade":
            data = result.get("data", {})
            notion_saved = save_trade_to_notion(data)
            status_text = "✅ Notion'ga saqlandi" if notion_saved else "⚠️ Notion'ga saqlanmadi"
            text = (
                f"📊 <b>Savdo tahlili:</b>\n\n"
                f"• <b>Juftlik:</b> {data.get('pair')}\n"
                f"• <b>Taymfreym:</b> {data.get('timeframe')}\n"
                f"• <b>Pozitsiya:</b> {data.get('position')}\n"
                f"• <b>Risk:</b> {data.get('risk')}%\n"
                f"• <b>Natija:</b> {data.get('result')}\n"
                f"• <b>Izoh:</b> {data.get('notes')}\n\n"
                f"{status_text}"
            )
            bot.edit_message_text(text, message.chat.id, status_msg.message_id, parse_mode="HTML")
        else:
            reply = result.get("reply", "Javob berishda xatolik yuz berdi.")
            bot.edit_message_text(reply, message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Xatolik yuz berdi: {e}", message.chat.id, status_msg.message_id)


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    status_msg = bot.reply_to(message, "Grafik tahlil qilinmoqda... ⏳")
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        image = Image.open(io.BytesIO(downloaded_file))

        caption = message.caption if message.caption else ""
        result = process_with_gemini(user_text=caption, pil_image=image)

        if result.get("type") == "trade":
            data = result.get("data", {})
            notion_saved = save_trade_to_notion(data)
            status_text = "✅ Notion'ga saqlandi" if notion_saved else "⚠️ Notion'ga saqlanmadi"
            text = (
                f"📊 <b>Grafik tahlili:</b>\n\n"
                f"• <b>Juftlik:</b> {data.get('pair')}\n"
                f"• <b>Taymfreym:</b> {data.get('timeframe')}\n"
                f"• <b>Pozitsiya:</b> {data.get('position')}\n"
                f"• <b>Risk:</b> {data.get('risk')}%\n"
                f"• <b>Natija:</b> {data.get('result')}\n"
                f"• <b>Izoh:</b> {data.get('notes')}\n\n"
                f"{status_text}"
            )
            bot.edit_message_text(text, message.chat.id, status_msg.message_id, parse_mode="HTML")
        else:
            reply = result.get("reply", "Rasm qabul qilindi.")
            bot.edit_message_text(reply, message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Xatolik yuz berdi: {e}", message.chat.id, status_msg.message_id)


class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")


def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()


if __name__ == "__main__":
    t = threading.Thread(target=run_http_server, daemon=True)
    t.start()
    bot.infinity_polling()
