import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from flask import Flask, request, abort

# 載入 .env 環境變數
load_dotenv()

# 引用 LINE Bot SDK v3
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent
)

# 引用 Google GenAI 官方 SDK
from google import genai
from google.genai import types

# 設定 Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 讀取環境變數
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("FLASK_DEBUG", "True").lower() in ("true", "1", "t")

# 檢查必備憑證
if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    logger.error("請確認 .env 檔案中已設定 LINE_CHANNEL_SECRET 與 LINE_CHANNEL_ACCESS_TOKEN！")
    sys.exit(1)

if not GEMINI_API_KEY:
    logger.error("請確認 .env 檔案中已設定 GEMINI_API_KEY！")
    sys.exit(1)

# 初始化 Flask App 與 LINE SDK
app = Flask(__name__)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 初始化 Google Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)
PRIMARY_MODEL = "gemini-3.5-flash-lite"
BACKUP_MODEL = "gemini-3.8-flash"


def get_current_taipei_time_str():
    """取得台灣標準時間 (UTC+8) 的精準年月日與星期字串"""
    taipei_tz = timezone(timedelta(hours=8))
    now = datetime.now(taipei_tz)
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return now.strftime(f"%Y年%m月%d日 {weekdays[now.weekday()]} %H:%M (台灣標準時間 UTC+8)")


def build_system_instruction():
    """動態生成帶有精準時間與高約束規則的 System Prompt"""
    current_time = get_current_taipei_time_str()
    return f"""
你是一款名為「OmniLine-AI」的全天候個人萬能助理，運行於 LINE 通訊軟體中，專為大學生與上班族提供即時、精準、高價值的協助。

【核心時空基準】
• 現在的精準時間是：{current_time}。
• 凡涉及今天、明天、昨天、星期幾、當前年份或日期的問題，請務必以此基準精準推算，絕不可出現時間認知偏差。

【最高準則：直奔主題，嚴禁廢話客套】
• 嚴禁任何冗長、繁瑣或無意義的客套開場白與結尾詞（例如：「您好！我是 OmniLine...」、「感謝您的訊息」、「很榮幸為您服務」等）。
• 請「第一句話直接給出核心結論或精準解答」！讓使用者在行動端能以最高效率獲取資訊。

【語言與格式排版】
• 語言規範：全程使用自然流暢的繁體中文（台灣習慣用語與拼寫）。
• 格式規範：針對行動裝置 LINE 聊天室排版優化。善用空行、列點符號（• 或 1. 2.）與適度 Emoji，文字俐落精煉，避免無分段大段文字。

【核心專業能力與防幻覺約束】
1. 【生活與捷運美食推薦（嚴格防幻覺）】：
   • 若詢問台北捷運周邊咖啡廳、餐廳或景點，必須僅推薦「真實存在、具體知名」的實體店家。
   • 回覆必須包含：真實店名、捷運站別與出口、特色亮點、實用資訊（如有無插座 🔌、大約價位）。
   • 嚴格禁止憑空捏造或杜撰虛假店名、地址或不存在的捷運出口！若特定冷門區域資料不確定，請如實說明。
2. 【文字與英文地道潤飾】：
   • 使用者提供英文句子時，提供「商務正式版」與「自然口語版」兩種修訂，並以 1~2 點簡明說明修訂原因。
3. 【多模態影像理解與外文翻譯】：
   • 若為圖片輸入，先指出主旨，接著提取圖中關鍵文字，外文請自動翻譯為繁體中文，最後條列重點。
"""


# 使用者多輪對話記憶快取 (以 user_id 為 Key 儲存 Chat Session)
user_chat_sessions = {}


def get_or_create_chat(user_id: str):
    """取得或建立使用者的 Gemini 多輪對話實例"""
    if user_id not in user_chat_sessions:
        logger.info(f"為使用者 {user_id} 建立新的對話 Session")
        chat = ai_client.chats.create(
            model=PRIMARY_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=build_system_instruction(),
                temperature=0.6,
            )
        )
        user_chat_sessions[user_id] = chat
    return user_chat_sessions[user_id]


def build_quick_reply():
    """建立快速回覆按鈕 (Quick Reply) - 優化引導性"""
    return QuickReply(
        items=[
            QuickReplyItem(action=MessageAction(label="💡 今日靈感", text="請給我一個今日生活或工作的小靈感！")),
            QuickReplyItem(action=MessageAction(label="☕ 捷運咖啡廳", text="推薦台北捷運周邊真實平價工作咖啡廳（含插座與站名）")),
            QuickReplyItem(action=MessageAction(label="📝 英文潤飾", text="請幫我潤飾英文句子：[請在此替換你的英文句子]")),
            QuickReplyItem(action=MessageAction(label="🧹 清除記憶", text="/reset")),
        ]
    )


@app.route("/", methods=["GET"])
def index():
    """健康檢查端點"""
    return {
        "status": "online",
        "service": "OmniLine-AI Assistant V2 (課後改善版)",
        "ai_engine": f"Google Gemini ({PRIMARY_MODEL})",
        "current_time": get_current_taipei_time_str(),
        "active_sessions": len(user_chat_sessions),
        "vision_enabled": True,
        "message": "LINE Webhook 伺服器正常運作中！V2 改善版功能已上線！"
    }, 200


@app.route("/callback", methods=["POST"])
def callback():
    """接收 LINE Webhook 事件"""
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        logger.warning("缺少 X-Line-Signature 標頭")
        abort(400)

    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("簽章驗證失敗 (InvalidSignatureError)")
        abort(400)
    except Exception as e:
        logger.error(f"處理 Webhook 時發生異常: {e}")
        abort(500)

    return "OK", 200


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    """處理文字訊息事件，串接 Gemini 產生智慧解答"""
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    reply_token = event.reply_token

    logger.info(f"[收到文字] 使用者 ({user_id}): {user_text}")

    # 特殊指令：重置對話記憶
    if user_text.lower() in ("/reset", "/clear", "重置", "清除記憶", "清除對話"):
        user_chat_sessions.pop(user_id, None)
        reply_msg = "對話記憶已清空。請問有什麼我可以協助您的？"
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=reply_msg, quick_reply=build_quick_reply())]
                )
            )
        return

    # Quick Reply 引導詞防呆處理
    if user_text == "請幫我潤飾英文句子：[請在此替換你的英文句子]":
        hint_msg = "請在冒號後面輸入你想修改的英文句子再發送給我喔！例如：\n\n請幫我潤飾英文句子：I want to know if you can come to the meeting tomorrow."
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=hint_msg, quick_reply=build_quick_reply())]
                )
            )
        return

    # 呼叫 Gemini AI 進行多輪對話推理
    ai_reply_text = ""
    try:
        chat = get_or_create_chat(user_id)
        response = chat.send_message(user_text)
        ai_reply_text = response.text.strip()
    except Exception as e:
        logger.error(f"Gemini 生成失敗，嘗試備援模型: {e}")
        try:
            backup_res = ai_client.models.generate_content(
                model=BACKUP_MODEL,
                contents=user_text,
                config=types.GenerateContentConfig(system_instruction=build_system_instruction())
            )
            ai_reply_text = backup_res.text.strip()
        except Exception as err2:
            logger.error(f"備援模型亦失敗: {err2}")
            ai_reply_text = "目前 AI 運算量較大，請稍後再試一次，或輸入「/reset」重整記憶。"

    # LINE 單則訊息上限為 5000 字元防呆截斷
    if len(ai_reply_text) > 4900:
        ai_reply_text = ai_reply_text[:4900] + "\n...(回覆文字過長已自動節錄)"

    # 回傳給 LINE 使用者，並附帶 Quick Reply 按鈕
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=ai_reply_text, quick_reply=build_quick_reply())]
            )
        )


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event: MessageEvent):
    """處理圖片訊息事件，使用 Gemini Vision 進行多模態辨識與翻譯"""
    user_id = event.source.user_id
    reply_token = event.reply_token
    message_id = event.message.id

    logger.info(f"[收到圖片] 使用者 ({user_id}): message_id={message_id}")

    # 1. 透過 LINE SDK 下載圖片二進位資料
    image_bytes = None
    try:
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            image_bytes = blob_api.get_message_content(message_id)
    except Exception as e:
        logger.error(f"下載圖片失敗: {e}")
        reply_error = "圖片下載失敗，請稍後再試一次！"
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=reply_error, quick_reply=build_quick_reply())]
                )
            )
        return

    # 2. 構建 Gemini Vision 多模態 Prompt
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    vision_prompt = f"""
當前精準時間：{get_current_taipei_time_str()}

請擔任專業的視覺影像解析與翻譯專家，對這張圖片進行詳細分析：
1. 【影像主旨】：一句話說明圖中內容（如外語菜單、白板手寫筆記、商品包裝、收據明細、書籍段落等）。
2. 【文字提取與翻譯 (OCR)】：
   • 提取圖中重要文字內容。
   • 若內容為外語（如英文、日文、韓文等），請自動翻譯為繁體中文（台灣常用語）。
3. 【重點摘要】：以條列式（•）整理出對使用者最關鍵、最實用的資訊。

請直奔主題、格式俐落整潔，嚴禁客套開場白！
"""

    ai_reply_text = ""
    try:
        response = ai_client.models.generate_content(
            model=PRIMARY_MODEL,
            contents=[image_part, vision_prompt],
            config=types.GenerateContentConfig(
                system_instruction=build_system_instruction(),
                temperature=0.4
            )
        )
        ai_reply_text = response.text.strip()
    except Exception as e:
        logger.error(f"Gemini Vision 解析失敗，嘗試備援模型: {e}")
        try:
            backup_res = ai_client.models.generate_content(
                model=BACKUP_MODEL,
                contents=[image_part, vision_prompt],
                config=types.GenerateContentConfig(
                    system_instruction=build_system_instruction()
                )
            )
            ai_reply_text = backup_res.text.strip()
        except Exception as err2:
            logger.error(f"備援 Vision 亦失敗: {err2}")
            ai_reply_text = "抱歉，目前圖片辨識處理較為繁忙，請稍後再試一次或換一張清晰的照片！"

    if len(ai_reply_text) > 4900:
        ai_reply_text = ai_reply_text[:4900] + "\n...(回覆文字過長已自動節錄)"

    # 3. 回覆使用者解析與翻譯結果
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=ai_reply_text, quick_reply=build_quick_reply())]
            )
        )


if __name__ == "__main__":
    logger.info(f"[*] OmniLine-AI V2 (Gemini Vision 升級版) 伺服器啟動於 http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
