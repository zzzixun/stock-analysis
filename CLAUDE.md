# TW-Quant-Desk Project Instructions

## 1. Project Overview
這是一套專為小資金散戶設計的台股量化戰情室，整合了「大盤正二（00631L）波段擇時」、「強勢個股雷達 ＋ 權證極短線突擊」、「美股/夜盤跨市場連動」與「Telegram/LINE 自動推播」。

## 2. Modular Specifications (細節規格參考)
在實作或修改各模組時，請務必參閱 `specs/` 目錄下的詳細規格書：
- 資料處理與跨市場抓取：`specs/spec_data_pipeline.md`
- 策略回測引擎與權證模擬：`specs/spec_core_strategies.md`
- 自動排程與戰情推播：`specs/spec_notifier_bot.md`

## 3. Global Development Rules
- 語言：Python 3.10+
- 程式碼品質：必須包含完整的 Type Hints、Docstrings 與 Clean Code 原則。
- 容錯機制：所有 API 抓取（FinMind, yfinance）均須加上 `try-except` 與離線 Cache / 假資料備援機制，避免網路異常導致程式崩潰。
- 執行指令：
  - 跑歷史回測：`python main.py --mode backtest`
  - 跑盤前預警：`python main.py --mode pre_market`
  - 跑盤後日報：`python main.py --mode post_market`