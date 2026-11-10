"""台股現貨、籌碼資料抓取與 00631L 拆股還原（規格：specs/spec_data_pipeline.md §1）。

資料源優先序：FinMind REST（免 token 可用；FINMIND_TOKEN 環境變數可提升額度）→ yfinance。
每個抓取都經過 `cached()`：線上抓取 → 失敗則讀離線快取 → 再失敗則產生假資料（df.attrs["source"] = "fake"），
確保網路異常時程式不會崩潰。"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

API = "https://api.finmindtrade.com/api/v4/data"
CACHE = Path(__file__).parent / "cache"
TRADING_DAYS = 252


# ---------------------------------------------------------------- 共用：抓取 / 快取 / 假資料
def finmind(dataset: str, data_id: str | None = None, start: str = "2018-01-01", end: str | None = None) -> pd.DataFrame:
    """呼叫 FinMind v4 API，失敗時丟出 RuntimeError。"""
    params = {"dataset": dataset, "start_date": start}
    if data_id:
        params["data_id"] = data_id
    if end:
        params["end_date"] = end
    if os.getenv("FINMIND_TOKEN"):
        params["token"] = os.environ["FINMIND_TOKEN"]
    js = requests.get(API, params=params, timeout=60).json()
    if js.get("status") != 200:
        raise RuntimeError(f"FinMind {dataset}: {js.get('msg')}")
    return pd.DataFrame(js["data"])


def yf_download(ticker: str, start: str) -> pd.DataFrame:
    """yfinance 單一標的日 K（欄位小寫，含 adj_close）。單執行緒以避免 tz cache 鎖死。"""
    import yfinance as yf

    df = yf.download(ticker, start=start, auto_adjust=False, progress=False, threads=False)
    if df.empty:
        raise RuntimeError(f"yfinance {ticker}: 無資料")
    df.columns = [(c[0] if isinstance(c, tuple) else c).lower().replace(" ", "_") for c in df.columns]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[["open", "high", "low", "close", "adj_close", "volume"]].astype(float)


def fake_ohlcv(start: str = "2018-01-01", end: str | None = None, s0: float = 100.0,
               vol: float = 0.25, seed: int = 0) -> pd.DataFrame:
    """幾何布朗運動假日 K，僅供離線備援 / 測試。"""
    idx = pd.bdate_range(start, end or pd.Timestamp.today().normalize())
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, vol / np.sqrt(TRADING_DAYS), len(idx))
    close = s0 * np.exp(np.cumsum(ret))
    open_ = close * np.exp(rng.normal(0, vol / 40, len(idx)))
    df = pd.DataFrame({"open": open_, "close": close,
                       "high": np.maximum(open_, close) * 1.005, "low": np.minimum(open_, close) * 0.995,
                       "volume": rng.integers(1_000, 50_000, len(idx)) * 1_000.0}, index=idx)
    df["adj_close"] = df["close"]
    return df


def cached(name: str, fetch: Callable[[], pd.DataFrame], refresh: bool = False,
           fallback: Callable[[], pd.DataFrame] | None = None) -> pd.DataFrame:
    """快取包裝：有快取且不 refresh → 讀快取；否則線上抓取並存檔；
    抓取失敗 → 讀舊快取；連快取都沒有 → fallback（假資料）。df.attrs["source"] 標記來源。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{name}.csv"
    if path.exists() and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.attrs["source"] = "cache"
        return df
    try:
        df = fetch()
        df.to_csv(path)
        df.attrs["source"] = "online"
        return df
    except Exception as e:  # noqa: BLE001 — 任何網路 / 解析錯誤都改走備援
        log.warning("%s 線上抓取失敗：%s", name, e)
        if path.exists():
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            df.attrs["source"] = "cache"
            return df
        if fallback is None:
            raise
        log.warning("%s 無快取，改用假資料", name)
        df = fallback()
        df.attrs["source"] = "fake"
        return df


# ---------------------------------------------------------------- 現貨日 K 與個股籌碼
def _finmind_price(stock_id: str, start: str) -> pd.DataFrame:
    df = finmind("TaiwanStockPrice", stock_id, start)
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    df.index = pd.to_datetime(df["date"])
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def load_price(stock_id: str, start: str = "2018-01-01") -> pd.DataFrame:
    """未還原日 K：FinMind 為主，yfinance 為輔（TAIEX 對應 ^TWII）。"""
    try:
        return _finmind_price(stock_id, start)
    except Exception as e:  # noqa: BLE001
        log.warning("FinMind 價格失敗（%s），改用 yfinance", e)
        ticker = "^TWII" if stock_id == "TAIEX" else f"{stock_id}.TW"
        return yf_download(ticker, start).drop(columns="adj_close")


def load_institutional(stock_id: str, start: str = "2018-01-01") -> pd.DataFrame:
    """三大法人買賣超（股），自營商避險獨立為 dealer_hedge。"""
    df = finmind("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start)
    df["net"] = df["buy"] - df["sell"]
    p = df.pivot_table(index="date", columns="name", values="net", aggfunc="sum").fillna(0)
    p.index = pd.to_datetime(p.index)
    out = pd.DataFrame(index=p.index)
    out["foreign"] = p.filter(like="Foreign").sum(axis=1)
    out["trust"] = p.get("Investment_Trust", 0)
    out["dealer_self"] = p.get("Dealer_self", 0)
    out["dealer_hedge"] = p.get("Dealer_Hedging", 0)
    return out


def adjust_splits(df: pd.DataFrame, min_jump: float = 2.5) -> pd.DataFrame:
    """以「前收 / 今開」偵測股票分割（例：0050 2025/06 1 拆 4），往前還原價格與成交量。"""
    df = df.copy()
    jump = df["close"].shift() / df["open"]
    for d in df.index[jump > min_jump]:
        f = round(jump[d])
        before = df.index < d
        df.loc[before, ["open", "high", "low", "close"]] /= f
        df.loc[before, "volume"] *= f
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """HV20、ATR14、5/10/20MA、布林通道與帶寬、量比。"""
    df = adjust_splits(df)
    c = df["close"]
    ret = np.log(c).diff()
    df["hv20"] = ret.rolling(20).std() * np.sqrt(TRADING_DAYS)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - c.shift()).abs(), (df["low"] - c.shift()).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    for n in (5, 10, 20):
        df[f"ma{n}"] = c.rolling(n).mean()
    sd = c.rolling(20).std()
    df["bb_up"], df["bb_dn"] = df["ma20"] + 2 * sd, df["ma20"] - 2 * sd
    df["bandwidth"] = (df["bb_up"] - df["bb_dn"]) / df["ma20"]
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean().shift()
    return df


def load(stock_id: str, start: str = "2018-01-01", refresh: bool = False) -> pd.DataFrame:
    """個股 / ETF：OHLCV + 法人籌碼（dealer_hedge）+ 技術指標。"""
    def fetch() -> pd.DataFrame:
        df = load_price(stock_id, start)
        try:
            df = df.join(load_institutional(stock_id, start), how="left")
        except Exception as e:  # noqa: BLE001
            log.warning("%s 法人資料失敗（%s），dealer_hedge 以 0 代替", stock_id, e)
        df["dealer_hedge"] = df.get("dealer_hedge", pd.Series(0.0, index=df.index)).fillna(0)
        return add_indicators(df[df["volume"] > 0].copy())

    def fake() -> pd.DataFrame:
        df = fake_ohlcv(start, seed=abs(hash(stock_id)) % 2**32).drop(columns="adj_close")
        df["dealer_hedge"] = 0.0
        return add_indicators(df)

    return cached(stock_id, fetch, refresh, fake)


def load_taiex(start: str = "2018-01-01", refresh: bool = False) -> pd.DataFrame:
    """加權指數日 K（FinMind TAIEX → yfinance ^TWII）。"""
    return cached(f"TAIEX_{start}", lambda: load_price("TAIEX", start), refresh, lambda: fake_ohlcv(start, s0=10_000, vol=0.18))


# ---------------------------------------------------------------- 大盤籌碼
def foreign_net_oi(raw: pd.DataFrame) -> pd.DataFrame:
    """由 FinMind TaiwanFuturesInstitutionalInvestors 原始資料算外資台指期淨未平倉口數。"""
    f = raw[raw["institutional_investors"].str.contains("外資")].copy()
    f["foreign_net_oi"] = f["long_open_interest_balance_volume"] - f["short_open_interest_balance_volume"]
    out = f.groupby("date")[["foreign_net_oi"]].sum()
    out.index = pd.to_datetime(out.index)
    return out


def load_futures_oi(start: str = "2018-06-01", refresh: bool = False) -> pd.DataFrame:
    """外資台指期（TX）淨未平倉口數。"""
    return cached("futures_oi", lambda: foreign_net_oi(finmind("TaiwanFuturesInstitutionalInvestors", "TX", start)),
                  refresh, lambda: pd.DataFrame({"foreign_net_oi": 0.0}, index=pd.bdate_range(start, pd.Timestamp.today())))


def load_margin(start: str = "2018-01-01", refresh: bool = False) -> pd.DataFrame:
    """大盤融資餘額（億元）。融資維持率需 FinMind 贊助等級，暫不提供。"""
    def fetch() -> pd.DataFrame:
        raw = finmind("TaiwanStockTotalMarginPurchaseShortSale", start=start)
        m = raw[raw["name"] == "MarginPurchaseMoney"].set_index("date")["TodayBalance"] / 1e8
        out = pd.DataFrame({"margin_balance_e8": m})
        out.index = pd.to_datetime(out.index)
        return out

    return cached("margin", fetch, refresh,
                  lambda: pd.DataFrame({"margin_balance_e8": np.nan}, index=pd.bdate_range(start, pd.Timestamp.today())))


# ---------------------------------------------------------------- 00631L 拆股還原
def fix_split_jumps(price: pd.Series, min_jump: float = 2.5) -> pd.Series:
    """收盤價序列的分割修正：單日倍數變化 > min_jump（或 < 1/min_jump）視為未還原的分割，往前除以整數倍數。"""
    price = price.copy()
    ratio = price.shift() / price
    for d in price.index[(ratio > min_jump) | (ratio < 1 / min_jump)]:
        f = round(ratio[d]) if ratio[d] > 1 else 1 / round(1 / ratio[d])
        price[price.index < d] /= f
    return price


def drop_bad_ticks(price: pd.Series, limit: float = 0.105) -> pd.Series:
    """台股漲跌幅上限 10%：單日報酬超過 ±limit 視為錯價，該日報酬以 0 取代後重新連乘（保留最新價位）。"""
    r = price.pct_change().fillna(0)
    r[r.abs() > limit] = 0.0
    chained = (1 + r).cumprod()
    return chained * price.iloc[-1] / chained.iloc[-1]


def cross_check(primary: pd.Series, reference: pd.Series, tol: float = 0.04) -> pd.Series:
    """以第二資料源校正錯價：同日報酬差 > tol（0050 單次除息約 1~3%，遠小於 4%）時改用 reference 的報酬。"""
    r = primary.pct_change().fillna(0)
    r_ref = reference.reindex(primary.index).pct_change(fill_method=None)
    bad = (r - r_ref).abs() > tol
    r[bad] = r_ref[bad]
    chained = (1 + r).cumprod()
    return chained * primary.iloc[-1] / chained.iloc[-1]


def load_0050_tr(start: str = "2014-10-01", refresh: bool = False) -> pd.Series:
    """0050 含息還原價（台灣 50 總報酬代理）：yfinance Adj Close，並以 FinMind 原始價交叉驗證錯價；
    yfinance 失敗改用 FinMind 原始價（僅還原分割、不含息）。"""
    def fetch() -> pd.DataFrame:
        try:
            ref = fix_split_jumps(_finmind_price("0050", start)["close"])
        except Exception as e:  # noqa: BLE001
            log.warning("FinMind 0050 失敗（%s），略過交叉驗證", e)
            ref = None
        try:
            s = fix_split_jumps(yf_download("0050.TW", start)["adj_close"])
            if ref is not None:
                s = cross_check(s, ref)
        except Exception as e:  # noqa: BLE001
            if ref is None:
                raise
            log.warning("yfinance 0050 失敗（%s），改用 FinMind 原始價（不含息）", e)
            s = ref
        return s.to_frame("adj_close")

    raw = cached(f"0050_tr_{start}", fetch, refresh, lambda: fake_ohlcv(start, s0=60, vol=0.18)[["adj_close"]])
    return drop_bad_ticks(raw["adj_close"].dropna())


BENCHMARKS: dict[str, Callable[[str, bool], pd.Series]] = {
    # 實測（2015-08 ~ 2026-11）：00631L 主要持有台指期，跟加權指數的 20 日追蹤誤差 5.2%，跟 0050 為 8.1%
    "TAIEX": lambda start, refresh: load_taiex(start, refresh)["close"],  # 加權價格指數（預設）
    "0050": load_0050_tr,                                      # 台灣 50 含息（00631L 名義追蹤標的）
}


def synthetic_letf_nav(index_close: pd.Series, leverage: float = 2.0, fee: float = 0.007,
                       roll_cost: float = 0.0) -> pd.Series:
    """合成正二淨值：每日 NAV ×= 1 + L × 指數日報酬 − (管理費 + 轉倉逆價差)/252，起點為 1。"""
    r = index_close.pct_change().fillna(0)
    daily = 1 + leverage * r - (fee + roll_cost) / TRADING_DAYS
    daily.iloc[0] = 1.0
    return daily.cumprod()


def detect_breaks(price: pd.Series, index_close: pd.Series, leverage: float = 2.0,
                  tol: float = 0.15) -> pd.DatetimeIndex:
    """價格斷層：當日 log 報酬與「L × 指數報酬」差距 > tol（正常追蹤誤差遠小於 1%，分割則高達 ±300%）。"""
    idx_r = index_close.reindex(price.index).pct_change()
    gap = (np.log(price / price.shift()) - np.log1p(leverage * idx_r)).abs()
    return price.index[gap > tol]


def fit_roll_cost(price: pd.Series, index_close: pd.Series, leverage: float = 2.0, fee: float = 0.007) -> float:
    """在無斷層區間用二分法擬合年化轉倉成本，使合成淨值的期末/期初倍數等於實際價格。"""
    idx = index_close.reindex(price.index).ffill()
    target = price.iloc[-1] / price.iloc[0]
    lo, hi = -0.20, 0.20
    for _ in range(60):
        mid = (lo + hi) / 2
        nav = synthetic_letf_nav(idx, leverage, fee, mid)
        lo, hi = (mid, hi) if nav.iloc[-1] > target else (lo, mid)
    return (lo + hi) / 2


def calibrate_letf(price: pd.Series, index_close: pd.Series, leverage: float = 2.0, fee: float = 0.007,
                   roll_cost: float | None = None, min_fit_days: int = TRADING_DAYS) -> pd.DataFrame:
    """00631L 還原價：取最後一個斷層之後的乾淨區段為真實價格，斷層之前（含上市前）以合成正二淨值
    向前銜接。roll_cost 為 None 時以乾淨區段擬合（區段 < min_fit_days 則用 0）。

    回傳欄位：close（還原後）、source（real / synthetic）、synthetic（全期間合成淨值，與 close 同尺度）。"""
    index_close = index_close.dropna()
    price = price.dropna()
    breaks = detect_breaks(price, index_close, leverage)
    clean_start = breaks.max() if len(breaks) else price.index[0]
    clean = price[price.index >= clean_start]
    if roll_cost is None:
        roll_cost = fit_roll_cost(clean, index_close, leverage, fee) if len(clean) >= min_fit_days else 0.0
    full_idx = index_close.index.union(price.index)
    nav = synthetic_letf_nav(index_close.reindex(full_idx).ffill().bfill(), leverage, fee, roll_cost)
    nav = nav * clean.iloc[0] / nav.loc[clean_start]  # 在銜接點對齊實際價格
    # 實際/合成比值向後延續：斷層前以銜接點對齊，乾淨區段內缺漏日 = 前一真實價 × 合成日報酬
    anchor = (clean / nav.reindex(clean.index)).reindex(full_idx).ffill().bfill()
    out = pd.DataFrame({"close": nav * anchor, "source": "synthetic", "synthetic": nav})
    out.loc[clean.index, "close"] = clean
    out.loc[clean.index, "source"] = "real"
    out.attrs.update(breaks=list(breaks), roll_cost=roll_cost, clean_start=clean_start)
    return out


def load_00631l(start: str = "2014-10-01", refresh: bool = False, leverage: float = 2.0,
                fee: float = 0.007, roll_cost: float | None = None, benchmark: str = "TAIEX") -> pd.DataFrame:
    """00631L 還原價：yfinance Adj Close 為主；失敗改 FinMind 原始價 + 分割偵測；
    仍有斷層的區段由 calibrate_letf() 以合成正二淨值補齊。start 早於上市日時以合成淨值延伸。
    benchmark：合成淨值的基準，"TAIEX"（加權價格指數，預設）或 "0050"（台灣 50 含息）。"""
    def fetch() -> pd.DataFrame:
        try:
            s = yf_download("00631L.TW", start)["adj_close"]
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance 00631L 失敗（%s），改用 FinMind + 分割偵測", e)
            s = adjust_splits(_finmind_price("00631L", start))["close"]
        return s.to_frame("adj_close")

    raw = cached("00631L_raw", fetch, refresh, lambda: pd.DataFrame(columns=["adj_close"], dtype=float))
    bench = BENCHMARKS[benchmark](start, refresh)
    if raw.empty:  # 完全離線：整段使用合成淨值
        nav = synthetic_letf_nav(bench, leverage, fee, roll_cost or 0.0) * 20
        return pd.DataFrame({"close": nav, "source": "synthetic", "synthetic": nav})
    out = calibrate_letf(raw["adj_close"], bench, leverage, fee, roll_cost)
    out.attrs["benchmark"] = benchmark
    return out
