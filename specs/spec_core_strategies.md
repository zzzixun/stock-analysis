# Spec 2: Core Strategies & Warrant Simulator

## 1. Black-Scholes 權證模擬與摩擦成本 (bs_warrant_sim.py)
- 實作標準 Black-Scholes 公式計算 Call/Put 理論價與 Greeks (Delta, Gamma, Vega, Theta)。
- **實質槓桿公式**：$\text{Effective Leverage} = \text{Delta} \times \frac{\text{Spot Price}}{\text{Warrant Price}}$
- **小資金真實摩擦成本模型**：
  - 單筆投入資金：預設 5,000 ~ 10,000 NTD。
  - 手續費低消：`max(成交金額 * 0.001425 * 證券電子下單折數, 最低手續費 20)` + 權證交易稅 `0.001`。
  - 造市價差滑價：進場買在委賣價（理論價 + 0.75%），出場賣在委買價（理論價 - 0.75%）。

## 2. 大盤正二擇時回測 (letf_backtester.py)
- 回測指標：CAGR、MDD、Sharpe Ratio、勝率。
- 策略清單：
  1. 基準買進持有 (Benchmark)
  2. 均線順勢濾網（站上 60MA 抱正二，跌破轉現金）
  3. 極端乖離抄底（負乖離超過 -10% 分批進場）
  4. 跨市場夜盤濾網（結合美股大漲與 ADR 溢價率作為隔日加碼濾網）

## 3. 個股雷達與權證篩選器 (stock_radar.py & warrant_screener.py)
- 個股綜合評分：RS相對強弱（5日/20日超額報酬） + VCP波動壓縮（BandWidth 百分位） + 自營商避險爆量。
- 權證篩選鐵律：剩餘天數 60~120 天、價外 10%～價內 5%、買賣價差比 <= 2.0%、單價 >= 0.7 元、在外流通比例 <= 80%。