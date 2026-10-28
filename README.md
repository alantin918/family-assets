# 家庭資產

夫妻資產（全部放在 Alan 名下）的每月快照與資金流儀表板。

- 線上儀表板：https://claude.ai/artifact/PAsjKMVLrS584LzbTJF1Ch
- `balances.csv`：各帳戶每次快照餘額（date, account, category, amount），空白視為 0
- `summary.csv`：原表的「總共」與「扣除共同基金後」（= Alan 可用）
- 共同基金 = total − after_joint
- `dashboard/index.html`：儀表板頁面原始碼備份（需在 claude.ai Dashboard 內執行）

## 每月更新流程
1. 在 Google 試算表「財務」分頁往右新增一欄（含最下方的日期列，如 `2026/11/6`）
2. 執行自動同步腳本抓取最新資料並更新兩份 CSV：
   ```bash
   python3 ~/家庭資產/sync.py
   ```
3. 上傳更新後的 `balances.csv` 與 `summary.csv` 到儀表板 → 圖表自動更新
