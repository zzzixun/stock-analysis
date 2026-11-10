# Spec 3: Notification & Scheduler Bot

## 1. 訊息推播模組 (bot/notifier.py)
- 支援 Telegram Bot API (`https://api.telegram.org/bot<TOKEN>/sendMessage`) 與 LINE Messaging API。
- 訊息格式支援 Markdown / HTML 語法（粗體、代號、數值對齊），確保在手機通知欄一目了然。

## 2. 每日自動排程與觸發情境
- **【盤後 16:30 戰情日報 (`--mode post_market`)】**：
  - 內容：大盤正二燈號（多頭/空頭/抄底區）、今日融資維持率、明日強勢個股 Top 5（附帶建議權證代號與實質槓桿）。
- **【盤前 08:30 跨市場預警 (`--mode pre_market`)】**：
  - 內容：昨晚費半漲跌幅、TSM ADR 溢價率、台指期夜盤表現、今日開盤多空開盤預判指南。

## 3. 雲端免開機自動化 (GitHub Actions)
- 在 `.github/workflows/daily_quant.yml` 設定 Cron 排程：
  - 台灣時間週一至週五 08:30 與 16:30 自動執行 `python main.py`，並透過環境變數安全讀取 Telegram Token 與 Chat ID。