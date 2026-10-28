# 家庭資產

夫妻資產（全部放在 Alan 名下）的每月快照與資金流儀表板。

- **GitHub Pages 加密線上儀表板**：https://alantin918.github.io/family-assets/ （需輸入通關密碼解鎖，瀏覽器自動記憶）
- **Telegram Bot**：`@ALANANNE_FAMILY_bot`（由 `launchd` `com.weilun.family-assets-bot` 背景常駐執行）
- **Claude Artifact 備份**：https://claude.ai/artifact/PAsjKMVLrS584LzbTJF1Ch

## 檔案說明
- `.env`：存放 Google Apps Script 網址、通關密碼與 Telegram Bot Token（已列入 `.gitignore` 絕不上傳）
- `balances.csv`：各帳戶每次快照餘額（本機保存，已列入 `.gitignore`）
- `summary.csv`：原表的「總共」與「扣除共同基金後」（= Alan 可用，本機保存，已列入 `.gitignore`）
- `data.enc.json`：由 `sync.py` 自動產生的 SHA-256 加密資料檔，供 GitHub Pages 前端解密繪圖
- `index.html`：獨立網頁版視覺化儀表板（含通關密碼鎖與深淺色主題）
- `sync.py`：一鍵從 Google 試算表「財務」分頁抓取最新快照、更新 CSV 並產生加密檔
- `bot.py`：Telegram 機器人主程式（支援 `/summary`、`/accounts`、`/history`、`/sync`、`/web`）

## 每月更新流程
1. 在 Google 試算表「財務」分頁往右新增一欄（含最下方的日期列，如 `2026/11/6`）
2. 在 Telegram 對 `@ALANANNE_FAMILY_bot` 點選 **「🔄 同步試算表」**（或執行 `python3 ~/家庭資產/sync.py --push`）
3. 自動更新本機 CSV、重新加密 `data.enc.json` 並推送更新 GitHub Pages 網站！
