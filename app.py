import os
import sys
import logging
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
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
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

# 全能個人萬能助理 System Instruction
SYSTEM_INSTRUCTION = """
你是一款名為「OmniLine-AI」的全天候個人萬能助理，運行於 LINE 通訊軟體中，專為大學生與上班族提供即時、精準、高價值的協助。

【核心原則與語氣風格】
1. 語言規範：全程使用繁體中文（台灣習慣用語），語氣親切自然、專業且有條理。
2. 格式規範：針對行動裝置 LINE 聊天室排版優化。善用空行、列點符號（• 或 1. 2.）與適度 Emoji，文字精煉易讀，避免冗長大段文字。
3. 核心能力：
   • 【文字與英文潤飾】：若使用者要求翻譯或潤飾英文，提供「商務正式」與「自然口語」兩種版本，並簡短說明修改重點。
   • 【生活與美食推薦】：若詢問景點、咖啡廳或美食，提供具體店名、特色亮點、預估價位或實用資訊（如有無插座、捷運站出口）。
   • 【學業與工作問答】：回答條理清晰，先給結論再說明步驟或論據。
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
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.7,
            )
        )
        user_chat_sessions[user_id] = chat
    return user_chat_sessions[user_id]


def build_quick_reply():
    """建立快速回覆按鈕 (Quick Reply)"""
    return QuickReply(
        items=[
            QuickReplyItem(action=MessageAction(label="💡 今日靈感", text="請給我一個今日生活或工作的小靈感！")),
            QuickReplyItem(action=MessageAction(label="☕ 捷運咖啡廳", text="推薦一間台北捷運附近適合工作的平價咖啡廳")),
            QuickReplyItem(action=MessageAction(label="📝 英文潤飾", text="幫我把這句改得更地道：I want to know your idea.")),
            QuickReplyItem(action=MessageAction(label="🧹 清除記憶", text="/reset")),
        ]
    )


@app.route("/", methods=["GET"])
def index():
    """健康檢查端點"""
    return {
        "status": "online",
        "service": "OmniLine-AI Assistant",
        "ai_engine": f"Google Gemini ({PRIMARY_MODEL})",
        "active_sessions": len(user_chat_sessions),
        "message": "LINE Webhook 伺服器正常運作中！AI 引擎已上線！"
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

    logger.info(f"[收到訊息] 使用者 ({user_id}): {user_text}")

    # 特殊指令：重置對話記憶
    if user_text.lower() in ("/reset", "/clear", "重置", "清除記憶", "清除對話"):
        user_chat_sessions.pop(user_id, None)
        reply_msg = "🧹 對話記憶已清空！我們重新開始吧。\n\n請問有什麼我可以協助您的？"
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=reply_msg, quick_reply=build_quick_reply())]
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
        logger.error(f"Gemini 生成失敗，嘗試備援模型或重啟 Session: {e}")
        try:
            # 備援嘗試：單輪重試
            backup_res = ai_client.models.generate_content(
                model=BACKUP_MODEL,
                contents=user_text,
                config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
            )
            ai_reply_text = backup_res.text.strip()
        except Exception as err2:
            logger.error(f"備援模型亦失敗: {err2}")
            ai_reply_text = "抱歉，目前 AI 運算量較大，請稍後再試一次，或輸入「/reset」重整對話記憶！"

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


if __name__ == "__main__":
    logger.info(f"[*] OmniLine-AI (Gemini 驅動) 伺服器啟動於 http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
