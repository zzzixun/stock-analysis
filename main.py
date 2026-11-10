"""TW-Quant-Desk CLI。
用法：python main.py --mode backtest [--refresh]
      python main.py --mode post_market | pre_market   （待實作）"""
import argparse
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import yaml

from core.bs_warrant_sim import BTConfig, CostModel, grid_backtest
from core.stock_radar import STRATEGIES
from data import market_fetcher

sys.stdout.reconfigure(encoding="utf-8")
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Noto Sans CJK TC", "PingFang TC", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).parent
OUT = ROOT / "results"
PCT_COLS = ["勝率", "平均報酬", "總報酬", "最大回撤", "破產風險"]


def load_config() -> dict:
    """讀 config.yaml，再以 config.local.yaml（機密）逐區塊覆蓋。"""
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    local = ROOT / "config.local.yaml"
    if local.exists():
        for k, v in (yaml.safe_load(local.read_text(encoding="utf-8")) or {}).items():
            cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
    return cfg


def fmt(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in PCT_COLS:
        if c in df:
            df[c] = df[c].map(lambda x: f"{x:.1%}")
    for c in ["期望值(元/筆)", "期末資金"]:
        if c in df:
            df[c] = df[c].map(lambda x: f"{x:,.0f}")
    for c in ["平均槓桿", "平均持有天"]:
        if c in df:
            df[c] = df[c].map(lambda x: f"{x:.1f}")
    return df


def heatmaps(results: dict[str, pd.DataFrame]) -> None:
    fig, axes = plt.subplots(1, len(results), figsize=(5 * len(results), 4))
    for ax, (name, tbl) in zip(axes, results.items()):
        m = tbl.pivot(index="價內外", columns="DTE", values="期望值(元/筆)").reindex(["ITM 5%", "ATM", "OTM 8%", "OTM 15%"])
        lim = max(abs(m.values).max(), 1)
        im = ax.imshow(m.values, cmap="RdYlGn", vmin=-lim, vmax=lim)
        ax.set_xticks(range(len(m.columns)), [f"{c}天" for c in m.columns])
        ax.set_yticks(range(len(m.index)), m.index)
        for (y, x), v in pd.DataFrame(m.values).stack().items():
            ax.text(x, y, f"{v:,.0f}", ha="center", va="center", fontsize=10)
        ax.set_title(f"{name}\n每筆期望值（元）")
        fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout()
    fig.savefig(OUT / "ev_heatmap.png", dpi=130)


def run_backtest(cfg: dict, refresh: bool) -> None:
    cap, cost, bt = cfg["capital"], cfg["cost"], cfg["backtest"]
    bcfg = BTConfig(initial_capital=cap["initial"], budget=cap["budget"],
                    max_holding_days=bt["max_holding_days"], take_profit=bt["take_profit"],
                    stop_loss=bt["stop_loss"], iv_premium=bt["iv_premium"], cost=CostModel(**cost))
    stocks, index = bt["stocks"], bt["index"]
    universe = {"A 壓縮突破+避險買超": stocks, "B 高檔破線+避險賣超": stocks, "C 大盤跳空突破": index}
    data = {s: market_fetcher.load(s, bt["start"], refresh) for s in stocks + index}
    OUT.mkdir(exist_ok=True)

    lines = ["# 權證極短線策略回測報告\n",
             f"資料期間：{bt['start']} ~ {max(df.index[-1] for df in data.values()).date()}　｜　"
             f"總資金 {cap['initial']:,.0f}、單筆 {cap['budget']:,.0f}、手續費 {cost['fee_discount']} 折低消 {cost['min_fee']} 元、"
             f"單邊價差 {cost['half_spread']:.2%}、IV crush {cost['iv_crush']:.2%}、"
             f"最長持有 {bcfg.max_holding_days} 天、停利 {bcfg.take_profit:.0%} / 停損 {bcfg.stop_loss:.0%}\n"]
    results = {}
    for name, (sig_fn, kind) in STRATEGIES.items():
        tbl, sigs = grid_backtest({s: data[s] for s in universe[name]}, sig_fn, kind, bcfg)
        results[name] = tbl
        tbl.to_csv(OUT / f"grid_{name.split()[0]}.csv", index=False, encoding="utf-8-sig")
        valid = tbl.dropna(subset=["期望值(元/筆)"])
        best = valid.loc[valid["期望值(元/筆)"].idxmax()] if len(valid) else None
        verdict = (f"**最佳規格：{best['價內外']} / {best['DTE']} 天**，每筆期望值 {best['期望值(元/筆)']:,.0f} 元、"
                   f"勝率 {best['勝率']:.0%}" if best is not None else "無有效交易")
        txt = fmt(tbl).to_markdown(index=False)
        print(f"\n=== {name}（{len(sigs)} 個訊號，標的：{', '.join(universe[name])}）===\n{txt}\n→ {verdict}")
        lines += [f"\n## {name}\n", f"訊號數 {len(sigs)}，標的 {', '.join(universe[name])}\n", txt, f"\n→ {verdict}\n"]

    heatmaps(results)
    lines.append("\n![EV heatmap](ev_heatmap.png)\n")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n報告已輸出：{OUT / 'report.md'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["backtest", "post_market", "pre_market"], default="backtest")
    ap.add_argument("--refresh", action="store_true", help="重新下載資料")
    a = ap.parse_args()
    cfg = load_config()
    if cfg["tokens"].get("finmind"):
        os.environ.setdefault("FINMIND_TOKEN", cfg["tokens"]["finmind"])
    if a.mode == "backtest":
        run_backtest(cfg, a.refresh)
    else:
        raise NotImplementedError(f"--mode {a.mode} 尚未實作（見 specs/spec_notifier_bot.md）")


if __name__ == "__main__":
    main()
