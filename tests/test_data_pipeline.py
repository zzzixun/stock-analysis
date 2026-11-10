"""specs/spec_data_pipeline.md 單元測試。預設全離線；`pytest -m network` 另跑真實資料整合測試。"""
import numpy as np
import pandas as pd
import pytest

from data import market_fetcher as mf
from data import us_night_fetcher as un


@pytest.fixture
def taiex() -> pd.Series:
    return mf.fake_ohlcv("2015-01-01", "2020-12-31", s0=9_000, vol=0.18, seed=1)["close"]


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "CACHE", tmp_path)


def _letf(taiex: pd.Series, roll: float = 0.0) -> pd.Series:
    return mf.synthetic_letf_nav(taiex, 2.0, 0.007, roll) * 20


# ---------------------------------------------------------------- 合成正二淨值
def test_synthetic_nav_formula():
    idx = pd.Series([100, 101, 99.99], index=pd.bdate_range("2024-01-01", periods=3))
    nav = mf.synthetic_letf_nav(idx, leverage=2, fee=0.0, roll_cost=0.0)
    assert nav.tolist() == pytest.approx([1.0, 1.02, 1.02 * (1 + 2 * (99.99 / 101 - 1))])


def test_synthetic_nav_deducts_fee():
    flat = pd.Series(100.0, index=pd.bdate_range("2024-01-01", periods=253))
    nav = mf.synthetic_letf_nav(flat, fee=0.007, roll_cost=0.003)
    assert nav.iloc[-1] == pytest.approx((1 - 0.01 / 252) ** 252)


def test_fit_roll_cost_recovers_known_drag(taiex):
    assert mf.fit_roll_cost(_letf(taiex, roll=0.012), taiex) == pytest.approx(0.012, abs=1e-6)


# ---------------------------------------------------------------- 斷層偵測與拆股還原
def test_detect_breaks_finds_split(taiex):
    price = _letf(taiex)
    split_day = price.index[800]
    price[price.index >= split_day] /= 22  # 模擬 1 拆 22
    assert list(mf.detect_breaks(price, taiex)) == [split_day]


def test_no_false_break_on_limit_moves(taiex):
    shocked = taiex.copy()
    shocked.iloc[500:] *= 0.9  # 指數單日 -10%，正二 -20% 屬正常
    assert len(mf.detect_breaks(_letf(shocked), shocked)) == 0


def test_calibrate_splices_synthetic_before_break(taiex):
    true = _letf(taiex, roll=0.01)
    broken = true.copy()
    split_day = true.index[300]
    broken[broken.index < split_day] *= 22  # 舊資料未還原（如 yfinance 2015 斷層）
    out = mf.calibrate_letf(broken, taiex)

    assert out.attrs["breaks"] == [split_day]
    assert (out.loc[out.index < split_day, "source"] == "synthetic").all()
    assert (out.loc[out.index >= split_day, "source"] == "real").all()
    assert out.attrs["roll_cost"] == pytest.approx(0.01, abs=1e-6)
    assert np.allclose(out["close"], true, rtol=1e-6)  # 還原後與真實序列一致
    assert len(mf.detect_breaks(out["close"], taiex)) == 0


def test_adjust_splits_restores_continuity():
    df = mf.fake_ohlcv("2024-01-01", "2024-06-30", s0=100, seed=3)
    d = df.index[60]
    df.loc[df.index >= d, ["open", "high", "low", "close"]] /= 4
    out = mf.adjust_splits(df)
    assert out["close"].pct_change().abs().max() < 0.2
    assert out.loc[df.index[0], "volume"] == df.loc[df.index[0], "volume"] * 4


def test_fix_split_jumps_series():
    s = pd.Series([100.0, 101, 25.5, 26], index=pd.bdate_range("2025-06-16", periods=4))  # 0050 式 1 拆 4
    assert mf.fix_split_jumps(s).tolist() == pytest.approx([25, 25.25, 25.5, 26])


def test_drop_bad_ticks():
    s = pd.Series([100.0, 101, 112, 113], index=pd.bdate_range("2024-01-01", periods=4))  # +10.9% 超過漲停 = 錯價
    out = mf.drop_bad_ticks(s)
    assert out.iloc[-1] == 113 and out.pct_change().abs().max() < 0.105


def test_cross_check_replaces_bad_tick():
    idx = pd.bdate_range("2014-10-21", periods=4)
    yf = pd.Series([100.0, 99, 106, 106.5], idx)    # 第 3 天 +7% 為錯價
    ref = pd.Series([50.0, 49.5, 49.0, 49.25], idx)  # 另一資料源：-1%
    out = mf.cross_check(yf, ref)
    assert out.pct_change().iloc[2] == pytest.approx(-0.0101, abs=1e-3) and out.iloc[-1] == 106.5


def test_load_00631l_benchmark_switch(monkeypatch, taiex):
    letf = _letf(taiex).to_frame("adj_close")
    monkeypatch.setattr(mf, "cached", lambda name, fetch, refresh=False, fallback=None: letf)
    monkeypatch.setitem(mf.BENCHMARKS, "TAIEX", lambda start, refresh: taiex)
    monkeypatch.setitem(mf.BENCHMARKS, "0050", lambda start, refresh: taiex * 0.01)
    for bm in ("TAIEX", "0050"):
        out = mf.load_00631l(benchmark=bm)
        assert out.attrs["benchmark"] == bm and (out["source"] == "real").all()


# ---------------------------------------------------------------- 籌碼
def test_foreign_net_oi():
    raw = pd.DataFrame({"date": ["2024-01-02"] * 3, "institutional_investors": ["自營商", "投信", "外資"],
                        "long_open_interest_balance_volume": [10, 20, 30_000],
                        "short_open_interest_balance_volume": [5, 5, 42_000]})
    assert mf.foreign_net_oi(raw)["foreign_net_oi"].iloc[0] == -12_000


# ---------------------------------------------------------------- 容錯：快取 / 假資料
def test_cached_falls_back_to_cache_then_fake(tmp_path):
    def boom() -> pd.DataFrame:
        raise ConnectionError("offline")

    fake = mf.cached("x", boom, fallback=lambda: mf.fake_ohlcv("2024-01-01", "2024-01-31"))
    assert fake.attrs["source"] == "fake" and len(fake) > 0

    good = mf.fake_ohlcv("2024-01-01", "2024-01-31", seed=9)
    mf.cached("y", lambda: good)
    again = mf.cached("y", boom, refresh=True)
    assert again.attrs["source"] == "cache"
    assert np.allclose(again["close"], good["close"])


def test_load_survives_offline(monkeypatch):
    def offline(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr(mf, "finmind", offline)
    monkeypatch.setattr(mf, "yf_download", offline)
    df = mf.load("2330", "2024-01-01")
    assert df.attrs["source"] == "fake" and {"hv20", "dealer_hedge", "bandwidth"} <= set(df.columns)


# ---------------------------------------------------------------- 跨市場
def test_adr_premium_formula():
    d = pd.to_datetime(["2024-01-02"])
    prem = un.adr_premium(pd.Series([110.0], d), pd.Series([31.0], d), pd.Series([600.0], d))
    assert prem.iloc[0] == pytest.approx((110 * 31 / (5 * 600) - 1) * 100)


def test_night_gap_alignment():
    raw = pd.DataFrame([
        ("2024-01-02", "position", "202401", 17_000, 900),
        ("2024-01-03", "after_market", "202401", 17_120, 500),   # 01-02 晚上 → 01-03 清晨
        ("2024-01-03", "after_market", "202402", 17_150, 10),    # 遠月，量小應忽略
        ("2024-01-03", "after_market", "202401/202402", 30, 99_999),  # 價差合約應排除
        ("2024-01-03", "position", "202401", 17_050, 800),
    ], columns=["date", "trading_session", "contract_date", "close", "volume"])
    out = un.night_gap(raw)
    row = out.loc["2024-01-03"]
    assert row["night_close"] == 17_120 and row["prev_day_close"] == 17_000
    assert row["night_gap"] == 120 and row["night_gap_pct"] == pytest.approx(120 / 17_000 * 100)


def test_cross_market_no_lookahead():
    us_d = pd.to_datetime(["2024-01-02", "2024-01-03"])
    us = pd.DataFrame({"sox": [100.0, 110.0], "nasdaq": [1.0, 1.0], "tsm": [100.0, 100.0], "usdtwd": [30.0, 30.0]}, us_d)
    tw = pd.Series([600.0, 600.0, 600.0], pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    night = pd.DataFrame({"night_gap": [50.0], "night_gap_pct": [0.3]}, pd.to_datetime(["2024-01-04"]))
    out = un.build_cross_market(tw.index, us, tw, night)
    # 01-03 美股收盤 +10% 只能出現在 01-04（台股 01-03 收盤時美股尚未開盤）
    assert np.isnan(out.loc["2024-01-03", "sox_ret"])
    assert out.loc["2024-01-04", "sox_ret"] == pytest.approx(10.0)
    assert out.loc["2024-01-04", "night_gap"] == 50.0


# ---------------------------------------------------------------- 真實資料整合測試（需網路）
@pytest.mark.network
@pytest.mark.parametrize("benchmark", ["TAIEX", "0050"])
def test_real_00631l_calibration(monkeypatch, benchmark):
    monkeypatch.setattr(mf, "CACHE", mf.Path(__file__).parents[1] / "data" / "cache")
    out = mf.load_00631l("2014-10-01", benchmark=benchmark)
    taiex = mf.load_taiex("2014-10-01")["close"]
    real = out[out["source"] == "real"]
    print(f"\n[{benchmark}] 斷層：{[d.date() for d in out.attrs['breaks']]}，乾淨區段起點 {out.attrs['clean_start'].date()}，"
          f"擬合轉倉成本 {out.attrs['roll_cost']:+.2%}/年，真實 {len(real)} 天 / 合成 {(out['source'] == 'synthetic').sum()} 天")
    a, b = np.log(real["close"]), np.log(real["synthetic"])
    corr = a.diff().corr(b.diff())
    te20 = (a.diff(20) - b.diff(20)).std() * np.sqrt(252 / 20)  # 月尺度追蹤誤差（濾掉 13:30/13:45 收盤時差雜訊）
    growth = (real["close"].iloc[-1] / real["close"].iloc[0], real["synthetic"].iloc[-1] / real["synthetic"].iloc[0])
    print(f"日報酬相關 {corr:.4f}，20 日年化追蹤誤差 {te20:.2%}，累積倍數 真實 {growth[0]:.2f}x / 合成 {growth[1]:.2f}x")
    assert len(mf.detect_breaks(out["close"], taiex)) == 0
    assert corr > 0.95 and te20 < 0.10
    assert growth[1] == pytest.approx(growth[0], rel=0.01)
