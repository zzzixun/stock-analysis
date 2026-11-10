"""Black-Scholes 定價 / Greeks、合成權證、小資金摩擦成本模型與合成權證網格回測。"""
from dataclasses import dataclass, field
from itertools import product
from math import erf, exp, log, sqrt, pi, floor, ceil

import numpy as np
import pandas as pd

LOT = 1000  # 1 張權證 = 1000 單位


def _ncdf(x):
    return 0.5 * (1 + erf(x / sqrt(2)))


def _npdf(x):
    return exp(-0.5 * x * x) / sqrt(2 * pi)


def black_scholes(S, K, T, r, sigma, kind="call"):
    """回傳每 1 股標的的理論價與 Greeks（T 為年，theta 為每日）。"""
    T = max(T, 1e-6)
    sigma = max(sigma, 1e-4)
    d1 = (log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    if kind == "call":
        price = S * _ncdf(d1) - K * exp(-r * T) * _ncdf(d2)
        delta = _ncdf(d1)
        theta = -S * _npdf(d1) * sigma / (2 * sqrt(T)) - r * K * exp(-r * T) * _ncdf(d2)
    else:
        price = K * exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)
        delta = _ncdf(d1) - 1
        theta = -S * _npdf(d1) * sigma / (2 * sqrt(T)) + r * K * exp(-r * T) * _ncdf(-d2)
    gamma = _npdf(d1) / (S * sigma * sqrt(T))
    price = max(price, 0.0)
    lev = abs(delta) * S / price if price > 1e-9 else 0.0  # 實質槓桿 = Delta × S / 權證價
    return {"price": price, "delta": delta, "gamma": gamma, "theta": theta / 365, "eff_leverage": lev}


def tick_size(p):
    """台灣權證升降單位。"""
    for bound, tick in ((5, 0.01), (10, 0.05), (50, 0.1), (100, 0.5), (500, 1)):
        if p < bound:
            return tick
    return 5


def round_tick(p, up):
    t = tick_size(p)
    n = p / t
    return round((ceil(n - 1e-9) if up else floor(n + 1e-9)) * t, 2)


@dataclass
class CostModel:
    fee_rate: float = 0.001425
    fee_discount: float = 0.6     # 手續費折數
    min_fee: float = 20           # 低消（部分網路券商為 1 元）
    tax_rate: float = 0.001       # 權證交易稅（賣出）
    half_spread: float = 0.0085   # 單邊造市價差：理論價 ±0.85%
    iv_crush: float = 0.0         # 出場時委買 IV 下調（絕對值，0.01 = 1 個 vol 點）

    def fee(self, amount):
        return max(amount * self.fee_rate * self.fee_discount, self.min_fee)

    def ask(self, theo):
        return round_tick(theo * (1 + self.half_spread), up=True)

    def bid(self, theo):
        return max(round_tick(theo * (1 - self.half_spread), up=False), 0.0)


@dataclass
class SyntheticWarrant:
    """一檔合成權證：發行時以 moneyness / DTE 決定履約價與到期日。"""
    kind: str          # "call" / "put"
    K: float
    dte_days: int      # 發行剩餘天數（日曆日）
    ratio: float       # 行使比例（1 單位權證 = ratio 股）
    r: float = 0.015

    @classmethod
    def issue(cls, S, kind, moneyness, dte_days, target_price=1.5, r=0.015, sigma=0.3):
        """moneyness > 0 = 價外，< 0 = 價內（例：0.08 = 價外 8%）。
        行使比例自動調整，讓發行價落在 target_price 左右（貼近實際權證報價區間）。"""
        K = S * (1 + moneyness) if kind == "call" else S * (1 - moneyness)
        per_share = black_scholes(S, K, dte_days / 365, r, sigma, kind)["price"]
        ratio = min(1.0, target_price / max(per_share, 1e-6))
        ratio = max(round(ratio, 3), 0.001)
        return cls(kind, K, dte_days, ratio, r)

    def quote(self, S, days_left, sigma):
        g = black_scholes(S, self.K, days_left / 365, self.r, sigma, self.kind)
        g["price"] *= self.ratio
        return g


def round_trip(theo_in, theo_out, budget, cost: CostModel):
    """以委賣價買入、委買價賣出，回傳 (損益, 投入成本, 張數) 。買不起 1 張回傳 None。"""
    ask, bid = cost.ask(theo_in), cost.bid(theo_out)
    lots = int(budget // (ask * LOT * (1 + cost.fee_rate * cost.fee_discount)))
    if lots < 1 or ask <= 0:
        return None
    units = lots * LOT
    buy_amt, sell_amt = ask * units, bid * units
    cost_in = buy_amt + cost.fee(buy_amt)
    proceeds = sell_amt - (cost.fee(sell_amt) if sell_amt > 0 else 0) - sell_amt * cost.tax_rate
    return proceeds - cost_in, cost_in, lots


# ---------------- 合成權證網格回測 ----------------
MONEYNESS = {"ITM 5%": -0.05, "ATM": 0.0, "OTM 8%": 0.08, "OTM 15%": 0.15}
DTES = [45, 90, 150]


@dataclass
class BTConfig:
    initial_capital: float = 30_000
    budget: float = 10_000          # 單筆投入
    max_holding_days: int = 5
    take_profit: float = 0.40       # 權證報酬 +40% 停利
    stop_loss: float = -0.30        # 權證報酬 -30% 停損
    iv_premium: float = 1.15        # 造市 IV = HV20 × 1.15
    r: float = 0.015
    cost: CostModel = field(default_factory=CostModel)


def simulate_trade(df, i, kind, mny, dte, cfg: BTConfig):
    """在第 i 根 K 收盤買進一檔合成權證，逐日以委買價檢查停利/停損/時間出場。"""
    row = df.iloc[i]
    sigma = row["hv20"] * cfg.iv_premium
    w = SyntheticWarrant.issue(row["close"], kind, mny, dte, r=cfg.r, sigma=sigma)
    entry = w.quote(row["close"], dte, sigma)
    theo_in, d0 = entry["price"], df.index[i]
    ask_in = cfg.cost.ask(theo_in)
    j_end = min(i + cfg.max_holding_days, len(df) - 1)
    for j in range(i + 1, j_end + 1):
        days_left = dte - (df.index[j] - d0).days
        theo = w.quote(df["close"].iloc[j], days_left, sigma - cfg.cost.iv_crush)["price"]
        ret = cfg.cost.bid(theo) / ask_in - 1
        if ret >= cfg.take_profit or ret <= cfg.stop_loss or j == j_end:
            break
    res = round_trip(theo_in, theo, cfg.budget, cfg.cost)
    if res is None or j == i:
        return None
    pnl, cost_in, lots = res
    return {"date": d0, "exit": df.index[j], "days": j - i, "lots": lots, "pnl": pnl,
            "ret": pnl / cost_in, "lev": entry["eff_leverage"],
            "spot_ret": df["close"].iloc[j] / row["close"] - 1}


def ruin_prob(rets, cfg: BTConfig, n_trades=50, n_sims=2000, ruin_level=0.5, seed=0):
    """Bootstrap 抽樣交易報酬，估計 n 筆內資金跌破初始 50% 的機率。"""
    if len(rets) == 0:
        return np.nan
    rng = np.random.default_rng(seed)
    draws = rng.choice(np.asarray(rets), size=(n_sims, n_trades))
    eq = np.full(n_sims, cfg.initial_capital)
    ruined = np.zeros(n_sims, bool)
    for k in range(n_trades):
        bet = np.minimum(cfg.budget, eq)
        eq = eq + bet * draws[:, k]
        ruined |= eq < cfg.initial_capital * ruin_level
    return ruined.mean()


def summarize(trades, cfg: BTConfig):
    if not trades:
        return {"交易數": 0}
    t = pd.DataFrame(trades).sort_values("date")
    # 依時間順序的資金曲線：每筆投入 min(單筆預算, 剩餘資金)，資金歸零即停止
    eq, curve = cfg.initial_capital, [cfg.initial_capital]
    for r in t["ret"]:
        eq = max(eq + min(cfg.budget, eq) * r, 0)
        curve.append(eq)
    curve = np.array(curve)
    mdd = (curve / np.maximum.accumulate(curve) - 1).min()
    return {
        "交易數": len(t),
        "勝率": (t["pnl"] > 0).mean(),
        "平均報酬": t["ret"].mean(),
        "期望值(元/筆)": t["pnl"].mean(),
        "期末資金": curve[-1],
        "總報酬": curve[-1] / cfg.initial_capital - 1,
        "最大回撤": mdd,
        "破產風險": ruin_prob(t["ret"], cfg),
        "平均槓桿": t["lev"].mean(),
        "平均持有天": t["days"].mean(),
    }


def grid_backtest(data: dict, sig_fn, kind: str, cfg: BTConfig = BTConfig()):
    """data: {股票代號: df}；sig_fn: df → 布林 Series。回傳 (各權證規格績效表, 訊號清單)。"""
    signals = [(sid, i) for sid, df in data.items()
               for i in np.flatnonzero(sig_fn(df).fillna(False).values) if df["hv20"].iloc[i] > 0]
    rows = []
    for (mname, mny), dte in product(MONEYNESS.items(), DTES):
        trades = [tr for sid, i in signals
                  if (tr := simulate_trade(data[sid], i, kind, mny, dte, cfg)) is not None]
        rows.append({"價內外": mname, "DTE": dte, **summarize(trades, cfg)})
    sig_list = [(sid, data[sid].index[i].date()) for sid, i in signals]
    return pd.DataFrame(rows), sig_list


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    S, sig = 1000, 0.30 * 1.15
    w = SyntheticWarrant.issue(S, "call", 0.08, 90, sigma=sig)
    q = w.quote(S, 90, sig)
    print(f"K={w.K:.0f} ratio={w.ratio} 理論價={q['price']:.3f} 實質槓桿={q['eff_leverage']:.2f} "
          f"Delta={q['delta']:.3f} 每日Theta(每單位)={q['theta'] * w.ratio:.4f}")
    cm = CostModel()
    print("委賣/委買:", cm.ask(q["price"]), cm.bid(q["price"]), "→ 立刻來回損益:", round_trip(q["price"], q["price"], 10000, cm))
