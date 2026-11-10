"""個股 / 大盤極短線訊號雷達（皆以當日收盤判斷，收盤進場）。
每個策略回傳布林 Series，搭配 STRATEGIES 註冊表給回測引擎使用。"""
from typing import Callable

import pandas as pd


def _hedge_spike(df: pd.DataFrame, mult: float = 2.0, sign: int = 1) -> pd.Series:
    """自營商避險買（sign=1）/ 賣（sign=-1）超 > 近 20 日平均絕對值 × mult。"""
    avg = df["dealer_hedge"].abs().rolling(20).mean().shift()
    return (sign * df["dealer_hedge"] > mult * avg) & (avg > 0)


def signal_a(df: pd.DataFrame, squeeze_q: float = 0.3, vol_mult: float = 1.5, hedge_mult: float = 2.0) -> pd.Series:
    """布林壓縮爆量突破 + 自營商避險買超 → Call"""
    bw_rank = df["bandwidth"].rolling(120).rank(pct=True)
    squeezed = bw_rank.rolling(5).min() <= squeeze_q
    return squeezed & (df["close"] > df["bb_up"]) & (df["vol_ratio"] > vol_mult) & _hedge_spike(df, hedge_mult, +1)


def signal_b(df: pd.DataFrame, vol_mult: float = 1.5, hedge_mult: float = 2.0, body: float = 0.02) -> pd.Series:
    """強勢股高檔爆量長黑破 5/10 日線 + 自營商避險大賣 → Put"""
    high_zone = df["close"].shift() >= 0.9 * df["high"].rolling(60).max().shift()
    long_black = (df["open"] - df["close"]) / df["open"] > body
    broke = (df["close"] < df["ma5"]) & (df["close"] < df["ma10"])
    return high_zone & long_black & broke & (df["vol_ratio"] > vol_mult) & _hedge_spike(df, hedge_mult, -1)


def signal_c(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """大盤跳空突破區間 + ATR 放大 → Call"""
    gap = df["open"] > df["high"].shift()
    breakout = df["close"] > df["high"].rolling(lookback).max().shift()
    atr_up = df["atr14"] > df["atr14"].shift(5)
    return gap & breakout & atr_up


# 名稱 → (訊號函式, 權證類型)
STRATEGIES: dict[str, tuple[Callable[[pd.DataFrame], pd.Series], str]] = {
    "A 壓縮突破+避險買超": (signal_a, "call"),
    "B 高檔破線+避險賣超": (signal_b, "put"),
    "C 大盤跳空突破": (signal_c, "call"),
}
