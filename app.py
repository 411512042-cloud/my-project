import os
import sys
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
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
)

# 讀取環境變數
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("FLASK_DEBUG", "True").lower() in ("true", "1", "t")

# 檢查必備憑證
if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    print("[Error] 請確認 .env 檔案中已設定 LINE_CHANNEL_SECRET 與 LINE_CHANNEL_ACCESS_TOKEN！", file=sys.stderr)
    sys.exit(1)

# 初始化 Flask App 與 LINE SDK
app = Flask(__name__)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


@app.route("/", methods=["GET"])
def index():
    """健康檢查端點 (可用於瀏覽器檢視或部署平台 Health Check)"""
    return {
        "status": "online",
        "service": "OmniLine-AI Assistant",
        "message": "LINE Webhook 伺服器正常運作中！請將 Webhook URL 設定為 /callback"
    }, 200


@app.route("/callback", methods=["POST"])
def callback():
    """接收 LINE 伺服器傳送過來的 Webhook 事件"""
    # 取得 X-Line-Signature 標頭
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        app.logger.warning("缺少 X-Line-Signature 標頭")
        abort(400)

    # 取得原始請求內容
    body = request.get_data(as_text=True)
    app.logger.info(f"收到 Webhook 請求: {body}")

    # 驗證簽章並指派事件給 Handler
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("簽章驗證失敗 (InvalidSignatureError)，請檢查 CHANNEL_SECRET 是否正確！")
        abort(400)
    except Exception as e:
        app.logger.error(f"處理 Webhook 時發生未預期錯誤: {e}")
        abort(500)

    return "OK", 200


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    """
    處理使用者傳送的文字訊息 (目前為 Echo 回應，後續階段將在此接入 Gemini / OpenAI)
    """
    user_text = event.message.text
    user_id = event.source.user_id
    reply_token = event.reply_token

    print(f"[收到訊息] 使用者 ({user_id}): {user_text}")

    # 基礎 Echo 回覆 (預留 AI 生成接口)
    echo_reply = f"【OmniLine 萬能助理】\n收到您的訊息：{user_text}\n\n(系統目前為連線測試模式，即將串接 Gemini AI！)"

    # 呼叫 LINE Messaging API 回傳訊息
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=echo_reply)]
            )
        )


if __name__ == "__main__":
    print(f"[*] OmniLine-AI Webhook 伺服器啟動於 http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
