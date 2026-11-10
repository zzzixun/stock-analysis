"""實盤權證篩選器：依鐵律剔除地雷後綜合評分，挑出前 3 名。
用法：python -m core.warrant_screener [sample_warrants.csv]"""
import sys

import pandas as pd

from core.bs_warrant_sim import black_scholes
from data.warrant_scraper import load_csv


def screen(df: pd.DataFrame, min_days: int = 60, max_spread: float = 0.02, max_outstanding: float = 0.80,
           min_price: float = 0.6, min_lev: float = 4.0, top: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()
    df["spread"] = (df["ask"] - df["bid"]) / df["ask"]
    df["eff_leverage"] = [abs(g["delta"]) * r.S * r.ratio / r.ask for r in df.itertuples()
                          for g in [black_scholes(r.S, r.K, r.days_left / 365, 0.015, r.bid_iv, r.kind)]]
    # 1. 剔除地雷
    ok = ((df["days_left"] >= min_days) & (df["spread"] <= max_spread)
          & (df["outstanding_ratio"] <= max_outstanding) & (df["ask"] >= min_price) & (df["eff_leverage"] >= min_lev))
    df["reject"] = ""
    for cond, why in [(df["days_left"] < min_days, "天數<60"), (df["spread"] > max_spread, "價差>2%"),
                      (df["outstanding_ratio"] > max_outstanding, "流通>80%"), (df["ask"] < min_price, "單價<0.6"),
                      (df["eff_leverage"] < min_lev, "槓桿<4")]:
        df.loc[cond, "reject"] += why + " "
    # 2. 綜合評分：槓桿高、價差小、委買 IV 相對同標的同類型最便宜
    df["iv_rel"] = df["bid_iv"] / df.groupby(["underlying", "kind"])["bid_iv"].transform("median")
    pool = df[ok].copy()
    pool["score"] = (pool["eff_leverage"].rank(pct=True) * 0.4
                     + (-pool["spread"]).rank(pct=True) * 0.3
                     + (-pool["iv_rel"]).rank(pct=True) * 0.3)
    return pool.sort_values("score", ascending=False).head(top), df[~ok]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    path = sys.argv[1] if len(sys.argv) > 1 else "sample_warrants.csv"
    best, rejected = screen(load_csv(path))
    cols = ["code", "name", "kind", "days_left", "ask", "spread", "bid_iv", "iv_rel", "eff_leverage"]
    print("★ 前 3 名：\n", best[cols + ["score"]].round(3).to_string(index=False))
    print("\n✗ 剔除：\n", rejected[["code", "name", "reject"]].to_string(index=False))
