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
    MessageAction,
    ShowLoadingAnimationRequest
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent
)

# 引用 Google GenAI 官方 SDK (用於 Vision 與備用備援)
from google import genai
from google.genai import types

# 引用 OpenAI 兼容 SDK (用於 OpenCode / 本地模型 API)
from openai import OpenAI

# 引用自研即時網路搜尋服務
from search_service import search_engine, should_trigger_search, format_search_context

# 設定 Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ===================================================
# 讀取環境變數
# ===================================================
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# OpenCode / 本地模型設定
AI_PROVIDER = os.getenv("AI_PROVIDER", "opencode").lower()
OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY")
OPENCODE_BASE_URL = os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen/v1")
OPENCODE_MODEL_NAME = os.getenv("OPENCODE_MODEL_NAME", "deepseek-chat")

PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("FLASK_DEBUG", "True").lower() in ("true", "1", "t")

# 檢查必備 LINE 憑證
if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    logger.error("請確認 .env 檔案中已設定 LINE_CHANNEL_SECRET 與 LINE_CHANNEL_ACCESS_TOKEN！")
    sys.exit(1)

# 初始化 Flask App 與 LINE SDK
app = Flask(__name__)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 初始化 OpenCode / 本地模型 Client (OpenAI 兼容規範)
opencode_client = None
if OPENCODE_API_KEY or OPENCODE_BASE_URL:
    try:
        opencode_client = OpenAI(
            api_key=OPENCODE_API_KEY or "dummy-key",
            base_url=OPENCODE_BASE_URL
        )
        logger.info(f"[*] OpenCode / 本地模型 Client 初始化成功 (Endpoint: {OPENCODE_BASE_URL}, Model: {OPENCODE_MODEL_NAME})")
    except Exception as e:
        logger.warning(f"[!] OpenCode Client 初始化失敗: {e}")

# 初始化 Google Gemini Client (用於 Vision 多模態及備援)
ai_client = None
PRIMARY_MODEL = "gemini-3.5-flash-lite"
BACKUP_MODEL = "gemini-3.8-flash"
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("[*] Google Gemini Client 初始化成功 (Vision & Backup 引擎備妥)")
    except Exception as e:
        logger.warning(f"[!] Gemini Client 初始化失敗: {e}")


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
2. 【即時網路檢索資料處理】：
   • 當提示詞中附帶「即時網路檢索真實資料」時，請務必嚴格依據檢索內容作答，並提煉重點，切勿脫離檢索事實。
3. 【文字與英文地道潤飾】：
   • 使用者提供英文句子時，提供「商務正式版」與「自然口語版」兩種修訂，並以 1~2 點簡明說明修訂原因。
4. 【多模態影像理解與外文翻譯】：
   • 若為圖片輸入，先指出主旨，接著提取圖中關鍵文字，外文請自動翻譯為繁體中文，最後條列重點。
"""


# 使用者多輪對話歷史紀錄
# OpenCode 使用: user_chat_histories[user_id] = [{"role": "user"|"assistant", "content": ...}]
user_chat_histories = {}
# Gemini Session 快取: user_chat_sessions[user_id] = GeminiChat
user_chat_sessions = {}


def get_or_create_gemini_chat(user_id: str):
    """取得或建立使用者的 Gemini 多輪對話實例 (備援用)"""
    if not ai_client:
        return None
    if user_id not in user_chat_sessions:
        logger.info(f"為使用者 {user_id} 建立新的 Gemini 對話 Session")
        chat = ai_client.chats.create(
            model=PRIMARY_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=build_system_instruction(),
                temperature=0.6,
            )
        )
        user_chat_sessions[user_id] = chat
    return user_chat_sessions[user_id]


def generate_opencode_response(user_id: str, user_text: str, search_context: str = "") -> str:
    """調用 OpenCode / 本地開源模型生成回覆 (結合即時搜尋 Context)"""
    if not opencode_client:
        raise RuntimeError("OpenCode 客戶端未建立或未設定 API Key/Base URL")

    if user_id not in user_chat_histories:
        user_chat_histories[user_id] = []

    history = user_chat_histories[user_id]

    # 組裝 Prompt (若有 Web Search 檢索內容則注入 RAG 上下文)
    prompt_for_model = user_text
    if search_context:
        prompt_for_model = f"""{search_context}

使用者提問：{user_text}

請嚴格根據上方提供的即時網路檢索真實資料回答，直奔重點結論，切勿編造！"""

    # 組裝對話歷史訊息
    messages = [{"role": "system", "content": build_system_instruction()}]
    # 加入最近對話歷史
    messages.extend(history)
    # 加入當前問題
    messages.append({"role": "user", "content": prompt_for_model})

    logger.info(f"[OpenCode] 發送請求至模型 {OPENCODE_MODEL_NAME}...")
    response = opencode_client.chat.completions.create(
        model=OPENCODE_MODEL_NAME,
        messages=messages,
        temperature=0.5,
        max_tokens=1500
    )

    ai_reply = response.choices[0].message.content.strip()

    # 更新記憶歷史 (保持最近 8 回合滑動視窗)
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": ai_reply})
    if len(history) > 16:
        user_chat_histories[user_id] = history[-16:]

    return ai_reply


def send_loading_animation(user_id: str, loading_seconds: int = 15):
    """向 LINE 用戶端發送正在輸入中的動態提示 (爭取 AI 聯網與推理時間)"""
    try:
        with ApiClient(configuration) as api_cli:
            line_bot_api = MessagingApi(api_cli)
            line_bot_api.show_loading_animation(
                ShowLoadingAnimationRequest(
                    chat_id=user_id,
                    loading_seconds=loading_seconds
                )
            )
    except Exception as e:
        logger.debug(f"發送 Loading 動畫略過: {e}")


def build_quick_reply():
    """建立快速回覆按鈕 (Quick Reply) - 優化引導性與即時搜尋"""
    return QuickReply(
        items=[
            QuickReplyItem(action=MessageAction(label="🔍 即時新聞速查", text="搜尋 台灣今天最重要的 3 則頭條新聞")),
            QuickReplyItem(action=MessageAction(label="☕ 捷運咖啡廳", text="推薦台北捷運周邊真實平價工作咖啡廳（含插座與站名）")),
            QuickReplyItem(action=MessageAction(label="💡 今日靈感", text="請給我一個今日生活或工作的小靈感！")),
            QuickReplyItem(action=MessageAction(label="📝 英文潤飾", text="請幫我潤飾英文句子：[請在此替換你的英文句子]")),
            QuickReplyItem(action=MessageAction(label="🧹 清除記憶", text="/reset")),
        ]
    )


@app.route("/", methods=["GET"])
def index():
    """健康檢查端點 - 顯示當前運行的模型架構與狀態"""
    active_engine = "OpenCode / Local Model" if (AI_PROVIDER == "opencode" and opencode_client) else f"Google Gemini ({PRIMARY_MODEL})"
    return {
        "status": "online",
        "service": "OmniLine-AI Assistant V3 (OpenCode + Web Search 升級版)",
        "active_ai_engine": active_engine,
        "opencode_endpoint": OPENCODE_BASE_URL if opencode_client else "Not Configured",
        "opencode_model": OPENCODE_MODEL_NAME if opencode_client else "N/A",
        "web_search_enabled": True,
        "current_time": get_current_taipei_time_str(),
        "active_user_sessions": max(len(user_chat_histories), len(user_chat_sessions)),
        "vision_enabled": ai_client is not None,
        "message": "LINE Webhook 伺服器正常運作中！OpenCode 與 Web Search 聯網防幻覺功能已就緒！"
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
    """處理文字訊息事件：結合 Web Search 檢索與 OpenCode / 本地模型推理"""
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    reply_token = event.reply_token

    logger.info(f"[收到文字] 使用者 ({user_id}): {user_text}")

    # 特殊指令：重置對話記憶
    if user_text.lower() in ("/reset", "/clear", "重置", "清除記憶", "清除對話"):
        user_chat_histories.pop(user_id, None)
        user_chat_sessions.pop(user_id, None)
        reply_msg = "對話記憶已清空。請問有什麼我可以協助您的？"
        with ApiClient(configuration) as api_cli:
            line_bot_api = MessagingApi(api_cli)
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
        with ApiClient(configuration) as api_cli:
            line_bot_api = MessagingApi(api_cli)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=hint_msg, quick_reply=build_quick_reply())]
                )
            )
        return

    # 1. 發送 LINE 輸入中等待動畫 (改善連線與搜尋體驗)
    send_loading_animation(user_id, loading_seconds=15)

    # 2. 意圖檢測與即時網路檢索 (Web Search)
    need_search, search_query = should_trigger_search(user_text)
    search_context = ""
    source_urls = []

    if need_search:
        logger.info(f"[Web Search] 觸發網路檢索: '{search_query}'")
        search_results = search_engine.search(search_query, max_results=3)
        if search_results:
            search_context = format_search_context(search_results)
            source_urls = [item["url"] for item in search_results if item.get("url")]
            logger.info(f"[Web Search] 檢索完成，獲取 {len(search_results)} 筆真實參考資訊")

    # 3. 呼叫核心 AI 模型進行推理 (優先 OpenCode，失敗自動降級 Gemini)
    ai_reply_text = ""
    used_engine = "OpenCode"

    # 嘗試調用 OpenCode / 本地模型 (若有設定金鑰或為本機端點)
    is_local = OPENCODE_BASE_URL and ("localhost" in OPENCODE_BASE_URL or "127.0.0.1" in OPENCODE_BASE_URL)
    can_use_opencode = bool(OPENCODE_API_KEY) or is_local

    if AI_PROVIDER == "opencode" and opencode_client and can_use_opencode:
        try:
            ai_reply_text = generate_opencode_response(user_id, user_text, search_context)
        except Exception as e:
            logger.error(f"OpenCode 調用失敗 ({e})，啟動 Gemini 備援機制...")
            used_engine = "Gemini Backup"

    # 若未啟用 OpenCode 或 OpenCode 調用失敗，使用 Gemini 備援
    if not ai_reply_text and ai_client:
        try:
            prompt_input = user_text
            if search_context:
                prompt_input = f"{search_context}\n\n使用者提問：{user_text}\n\n請依據搜尋結果直接回答："

            # 嘗試使用 Gemini 多輪 Session 或直接生成
            chat = get_or_create_gemini_chat(user_id)
            if chat and not search_context:
                response = chat.send_message(prompt_input)
                ai_reply_text = response.text.strip()
            else:
                res = ai_client.models.generate_content(
                    model=PRIMARY_MODEL,
                    contents=prompt_input,
                    config=types.GenerateContentConfig(
                        system_instruction=build_system_instruction(),
                        temperature=0.5
                    )
                )
                ai_reply_text = res.text.strip()
        except Exception as gemini_err:
            logger.error(f"Gemini 備援亦失敗: {gemini_err}")
            try:
                # 二度備援：使用 BACKUP_MODEL
                backup_res = ai_client.models.generate_content(
                    model=BACKUP_MODEL,
                    contents=user_text,
                    config=types.GenerateContentConfig(system_instruction=build_system_instruction())
                )
                ai_reply_text = backup_res.text.strip()
            except Exception as final_err:
                logger.error(f"所有 AI 引擎皆異常: {final_err}")
                ai_reply_text = "目前 AI 運算量較大或網路不穩定，請稍後再試一次，或輸入「/reset」重整記憶。"

    # 若有搜尋來源，在文末附加可信來源連結
    if source_urls and len(ai_reply_text) > 0:
        ai_reply_text += "\n\n🌐 即時資料來源："
        for idx, u in enumerate(source_urls[:2], start=1):
            ai_reply_text += f"\n• {u}"

    # LINE 單則訊息上限為 5000 字元防呆截斷
    if len(ai_reply_text) > 4900:
        ai_reply_text = ai_reply_text[:4900] + "\n...(回覆文字過長已自動節錄)"

    # 4. 回傳給 LINE 使用者，並附帶 Quick Reply 按鈕
    with ApiClient(configuration) as api_cli:
        line_bot_api = MessagingApi(api_cli)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=ai_reply_text, quick_reply=build_quick_reply())]
            )
        )


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event: MessageEvent):
    """處理圖片訊息事件：維持 V2 原有 Gemini Vision 多模態辨識與翻譯邏輯"""
    user_id = event.source.user_id
    reply_token = event.reply_token
    message_id = event.message.id

    logger.info(f"[收到圖片] 使用者 ({user_id}): message_id={message_id}")

    # 發送 Loading 動畫提示
    send_loading_animation(user_id, loading_seconds=15)

    # 1. 透過 LINE SDK 下載圖片二進位資料
    image_bytes = None
    try:
        with ApiClient(configuration) as api_cli:
            blob_api = MessagingApiBlob(api_cli)
            image_bytes = blob_api.get_message_content(message_id)
    except Exception as e:
        logger.error(f"下載圖片失敗: {e}")
        reply_error = "圖片下載失敗，請稍後再試一次！"
        with ApiClient(configuration) as api_cli:
            line_bot_api = MessagingApi(api_cli)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=reply_error, quick_reply=build_quick_reply())]
                )
            )
        return

    # 2. 構建 Gemini Vision 多模態 Prompt
    if not ai_client:
        reply_error = "目前伺服器尚未配置 Vision 多模態模型金鑰，無法分析圖片。"
        with ApiClient(configuration) as api_cli:
            line_bot_api = MessagingApi(api_cli)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=reply_error, quick_reply=build_quick_reply())]
                )
            )
        return

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
    with ApiClient(configuration) as api_cli:
        line_bot_api = MessagingApi(api_cli)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=ai_reply_text, quick_reply=build_quick_reply())]
            )
        )


if __name__ == "__main__":
    logger.info(f"[*] OmniLine-AI V3 (OpenCode + Web Search 升級版) 啟動於 http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
