"""美股、匯率、台指期夜盤跨市場資料（規格：specs/spec_data_pipeline.md §2）。

時間對齊：美股日期 D 的收盤（台灣時間 D+1 清晨）只會影響台股「D 之後」的交易日，
因此 build_cross_market() 以 merge_asof(allow_exact_matches=False) 取「嚴格早於」台股日的美股資料，避免前視偏差。"""
from __future__ import annotations

import pandas as pd

from data.market_fetcher import cached, fake_ohlcv, finmind, load, yf_download

US_TICKERS: dict[str, str] = {"sox": "^SOX", "nasdaq": "^IXIC", "tsm": "TSM", "usdtwd": "TWD=X"}
ADR_RATIO = 5  # 1 股 TSM ADR = 5 股 2330


def load_us_markets(start: str = "2018-01-01", refresh: bool = False) -> pd.DataFrame:
    """費半、那斯達克、TSM ADR、USD/TWD 收盤價（欄位：sox, nasdaq, tsm, usdtwd）。"""
    def fetch() -> pd.DataFrame:
        return pd.concat({k: yf_download(t, start)["close"] for k, t in US_TICKERS.items()}, axis=1)

    def fake() -> pd.DataFrame:
        s0 = {"sox": 5_000, "nasdaq": 18_000, "tsm": 200, "usdtwd": 31}
        return pd.concat({k: fake_ohlcv(start, s0=v, vol=0.05 if k == "usdtwd" else 0.3, seed=i)["close"]
                          for i, (k, v) in enumerate(s0.items())}, axis=1)

    return cached(f"us_markets_{start}", fetch, refresh, fake)


def adr_premium(tsm: pd.Series, usdtwd: pd.Series, tw2330: pd.Series) -> pd.Series:
    """TSM ADR 溢價率（%）= (TSM × USD/TWD ÷ (5 × 2330) − 1) × 100。三者依日期對齊（同日 ffill 匯率）。"""
    df = pd.concat({"tsm": tsm, "fx": usdtwd, "tw": tw2330}, axis=1).sort_index()
    df["fx"] = df["fx"].ffill()
    df = df.dropna(subset=["tsm", "tw"])
    return ((df["tsm"] * df["fx"]) / (ADR_RATIO * df["tw"]) - 1) * 100


def night_gap(raw: pd.DataFrame) -> pd.DataFrame:
    """由 FinMind TaiwanFuturesDaily（TX）原始資料計算夜盤跳空。

    FinMind 中日期 D 的 after_market 列 = D-1 晚上 15:00 開盤、D 清晨 05:00 收盤的夜盤。
    跳空點數 = 該夜盤收盤 − 前一交易日（D-1）日盤收盤，標記在 D（即它要預判的那個開盤日）。
    每日每盤別取成交量最大的單月合約（近月），排除價差合約（contract_date 含 '/'）。"""
    df = raw[~raw["contract_date"].astype(str).str.contains("/")].copy()
    df = df.sort_values("volume").groupby(["date", "trading_session"]).tail(1)
    p = df.pivot(index="date", columns="trading_session", values="close")
    p.index = pd.to_datetime(p.index)
    p = p.sort_index()
    out = pd.DataFrame(index=p.index)
    out["night_close"] = p.get("after_market")
    out["prev_day_close"] = p.get("position").shift()
    out["night_gap"] = out["night_close"] - out["prev_day_close"]
    out["night_gap_pct"] = out["night_gap"] / out["prev_day_close"] * 100
    return out.dropna(subset=["night_close"])


def load_tx_night(start: str = "2018-01-01", refresh: bool = False) -> pd.DataFrame:
    """台指期夜盤收盤與跳空點數（欄位：night_close, prev_day_close, night_gap, night_gap_pct）。"""
    return cached(f"tx_night_{start}", lambda: night_gap(finmind("TaiwanFuturesDaily", "TX", start)), refresh,
                  lambda: pd.DataFrame(columns=["night_close", "prev_day_close", "night_gap", "night_gap_pct"],
                                       index=pd.DatetimeIndex([]), dtype=float))


def build_cross_market(tw_dates: pd.DatetimeIndex, us: pd.DataFrame, tw2330: pd.Series,
                       night: pd.DataFrame) -> pd.DataFrame:
    """以台股交易日為索引，合併前一個美股交易日的漲跌幅 / ADR 溢價率，以及當日開盤前的夜盤跳空。"""
    us = us.sort_index()
    feat = pd.DataFrame(index=us.index)
    feat["sox_ret"] = us["sox"].pct_change(fill_method=None) * 100
    feat["nasdaq_ret"] = us["nasdaq"].pct_change(fill_method=None) * 100
    feat["adr_premium"] = adr_premium(us["tsm"], us["usdtwd"], tw2330)
    feat = feat.dropna(how="all").rename_axis("us_date").reset_index()
    feat["us_date"] = feat["us_date"].astype("datetime64[ns]")  # 快取 / yfinance 的時間精度可能不同
    base = pd.DataFrame({"date": pd.DatetimeIndex(tw_dates).sort_values().astype("datetime64[ns]")})
    out = pd.merge_asof(base, feat, left_on="date", right_on="us_date", allow_exact_matches=False)
    return out.set_index("date").join(night[["night_gap", "night_gap_pct"]], how="left")


def load_cross_market(start: str = "2018-01-01", refresh: bool = False) -> pd.DataFrame:
    """一鍵取得跨市場特徵表（台股交易日索引）。"""
    tw = load("2330", start, refresh)
    return build_cross_market(tw.index, load_us_markets(start, refresh), tw["close"], load_tx_night(start, refresh))


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    print(load_cross_market().tail(5).round(2).to_string())
