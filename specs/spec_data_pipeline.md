# Spec 1: Data Pipeline & Cross-Market Fetcher

## 1. 台股現貨與籌碼 (FinMind / yfinance)
- 抓取範圍：加權指數 (`^TWII`)、0050 (`0050.TW`)、00631L (`00631L.TW`) 及指定個股。
- **00631L 拆股還原 (Stock Split Calibration)**：
  - 必須偵測歷史股價中的除權息與分割事件，使用 `yfinance` 的 `Adj Close` 進行還原。
  - 若還原價格有斷層，必須實作「合成正二淨值公式」備援：以加權指數日報酬率 $\times 2$ 扣除 0.7% 年化管理費與預估期貨轉倉逆價差。
- **籌碼面指標**：抓取外資期貨未平倉口數、大盤融資維持率，以及個股的 **自營商避險買賣超 (Dealer Hedging)**。

## 2. 美股與夜盤跨市場連動 (us_night_fetcher.py)
- 抓取標的：費城半導體 (`^SOX`)、那斯達克 (`^IXIC`)、台積電 ADR (`TSM`)、匯率 (`TWD=X`)、台指期夜盤收盤價。
- **台積電 ADR 溢價率計算公式**：
  $$\text{ADR 溢價率 (\%)} = \left( \frac{\text{TSM 收盤價} \times \text{USD/TWD 匯率}}{5 \times \text{2330 台股收盤價}} - 1 \right) \times 100\%$$
- **夜盤跳空預期**：計算期交所夜盤收盤價相對於下午台股日盤收盤價的點數差，作為隔日開盤強弱判斷依據。