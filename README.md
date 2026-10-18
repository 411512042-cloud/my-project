# LINE Bot 輕量版（Ollama 本地小模型）專案

這個專案是基於 Python 與 Ollama 開發的 LINE Bot，可以在本地電腦執行開源小模型並進行測試。

---

## 🛠️ 1. 怎麼裝 (Installation)

1. **安裝 Python**：確認電腦已安裝 Python 3.10+。
2. **安裝 Ollama 與模型**：
   * 下載並安裝 [Ollama](https://ollama.com)。
   * 下載輕量模型：
     ```bash
     ollama pull gemma4:e2b
     ```
3. **下載專案檔案**：
   ```bash
   git clone [https://github.com/411512042-cloud/my-project.git](https://github.com/411512042-cloud/my-project.git)
  ## 🛠️ 2. 怎麼跑 
開啟 CMD 視窗並進入專案目錄：

DOS
cd C:\Users\Administrator\my-project
執行主程式：

DOS
python line_bot_lite.py
依提示貼上 LINE 的 Channel access token 與 Channel secret 進行驗證。
## 🛠️ 3. 我改了什麼及實測狀況

模型選用：使用 gemma4:e2b 輕量版本地模型。

關卡 A 測試：已成功安裝 Ollama 並載入模型（顯示 ✅ 模型可以用了）。

關卡 B 測試：於自動切換 Webhook 時因 Cloudflare 網址問題回傳 400 錯誤（Invalid webhook endpoint URL），已依據規定於截圖與作業說明中老實記錄卡關狀況。



